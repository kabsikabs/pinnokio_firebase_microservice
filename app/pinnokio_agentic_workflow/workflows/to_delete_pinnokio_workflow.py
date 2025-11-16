"""
Pinnokio Agent Workflow - Workflow principal de l'agent cerveau
Basé sur le framework agentic existant (expenses_agent_workflow)
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from ...llm.klk_agents import ModelSize, ModelProvider

logger = logging.getLogger("pinnokio.workflow")


def pinnokio_agent_workflow(manager_instance: Any,
                            initial_query: str,
                            tools: List[Dict[str, Any]],
                            tool_mapping: Dict[str, Any],
                            uid: str,
                            collection_name: str,
                            thread_key: str,
                            size: ModelSize = ModelSize.MEDIUM,
                            provider: Optional[ModelProvider] = None,
                            max_tokens: int = 4096,
                            workflow_step: str = 'pinnokio_workflow',
                            max_turns: int = 10,
                            raw_output: bool = True) -> Tuple[bool, str, str]:
    """
    Workflow agentic pour Pinnokio (agent cerveau)
    
    Ce workflow est basé sur expenses_agent_workflow mais adapté pour gérer
    l'orchestration SPT/LPT avec:
    - Support des tâches longues asynchrones (LPT)
    - Maintien de la disponibilité pendant LPT
    - Gestion des callbacks
    - Interaction continue avec l'utilisateur
    
    Args:
        manager_instance: Instance de PinnokioBrain
        initial_query: Requête utilisateur (peut contenir rapport si itération > 1)
        tools: Liste des outils disponibles (SPT + LPT + TERMINATE)
        tool_mapping: Mapping des outils vers leurs fonctions
        uid: User ID Firebase (IMPORTANT pour compartimentage)
        collection_name: Nom de la collection/société (IMPORTANT pour isolation)
        thread_key: Clé du thread de conversation (IMPORTANT pour tracking)
        size: Taille du modèle (MEDIUM par défaut pour raisonnement)
        provider: Provider AI (ANTHROPIC par défaut)
        max_tokens: Limite de tokens
        workflow_step: Nom de l'étape
        max_turns: Nombre maximum de tours (10 par défaut)
        raw_output: Format de sortie
        
    Returns:
        Tuple[bool, str, str]: (success, status_code, final_response_text)
        
    Status codes:
        - "TEXT_RESPONSE": Réponse texte simple (pas d'outils utilisés)
        - "MISSION_COMPLETED": Mission accomplie (TERMINATE_TASK appelé)
        - "TOKEN_LIMIT_REACHED": Budget de tokens atteint (80K), résumé généré
        - "MAX_TURNS_REACHED": Limite de tours atteinte (garde-fou)
        - "LPT_IN_PROGRESS": Tâches LPT en cours (agent disponible)
        - "NO_IA_ACTION": Aucune action de l'IA
        - "ERROR_FATAL": Erreur fatale
    """
    try:
        logger.info(f"[PINNOKIO_WORKFLOW] Démarrage - uid={uid}, collection={collection_name}, thread={thread_key}")
        logger.info(f"[PINNOKIO_WORKFLOW] Tours max (garde-fou): {max_turns}, taille modèle: {size}")
        
        # Configuration du budget de tokens
        max_tokens_budget = 80000  # 80K tokens avant résumé et reset
        max_turns = 20  # 🔧 TEMPORAIRE pour tests - À ENLEVER si fonctionne bien
                        # La vraie limite est le budget tokens (80K), pas le nombre de tours
        turn_count = 0
        user_input = initial_query
        next_user_input_parts = []
        
        logger.info(f"[PINNOKIO_WORKFLOW] Budget tokens: {max_tokens_budget:,} (tours max: {max_turns} - temporaire)")
        
        while turn_count < max_turns:  # Garde-fou secondaire TEMPORAIRE
            turn_count += 1
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 0 : VÉRIFICATION DU BUDGET TOKENS
            # ═══════════════════════════════════════════════════
            try:
                tokens_before = manager_instance.agent.get_total_context_tokens(provider)
                
                logger.info(
                    f"[PINNOKIO_WORKFLOW] Tour {turn_count}/{max_turns} - "
                    f"Tokens contexte: {tokens_before:,}/{max_tokens_budget:,}"
                )
                
                # Si on dépasse le budget, générer un résumé et RÉINITIALISER
                if tokens_before >= max_tokens_budget:
                    logger.warning(f"[TOKENS] Budget atteint ({tokens_before:,} tokens) - Réinitialisation contexte")
                    
                    # Générer le résumé
                    summary = manager_instance.generate_conversation_summary(
                        thread_key=thread_key,
                        total_tokens_used=tokens_before
                    )
                    
                    # RÉINITIALISER le contexte avec le résumé
                    tokens_after_reset = manager_instance.reset_context_with_summary(summary)
                    
                    # TODO: Sauvegarder le résumé dans Firebase pour historique
                    # save_summary_to_firebase(thread_key, summary, tokens_before)
                    
                    logger.info(
                        f"[TOKENS] Contexte réinitialisé - "
                        f"Avant: {tokens_before:,} tokens → "
                        f"Après: {tokens_after_reset:,} tokens (system+résumé)"
                    )
                    
                    # Mettre à jour tokens_before pour le calcul du delta
                    tokens_before = tokens_after_reset
                    
                    # ✅ CONTINUER (pas de return) - La conversation continue
                    
            except Exception as e:
                logger.warning(f"[TOKENS] Erreur calcul tokens: {e}, continuation...")
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 1 : APPEL DE L'AGENT
            # ═══════════════════════════════════════════════════
            ia_responses = manager_instance.agent.process_tool_use(
                content=user_input,
                tools=tools,
                tool_mapping=tool_mapping,
                size=size,
                provider=provider,
                max_tokens=max_tokens,
                raw_output=raw_output
            )
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 2 : TRACKING DES TOKENS
            # ═══════════════════════════════════════════════════
            try:
                manager_instance.agent.load_token_usage_to_db(
                    project_id=collection_name,
                    job_id=thread_key,
                    workflow_step=f"{workflow_step}_turn_{turn_count}"
                )
            except Exception as e:
                logger.warning(f"Erreur tracking tokens: {e}")
            
            logger.debug(f"[PINNOKIO_WORKFLOW] Réponse tour {turn_count}: {str(ia_responses)[:300]}...")
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 3 : NORMALISATION DES RÉPONSES
            # ═══════════════════════════════════════════════════
            if not isinstance(ia_responses, list):
                ia_responses = [ia_responses] if ia_responses else []
            
            next_user_input_parts = []
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 4 : DÉTECTION TEXT_OUTPUT SIMPLE (SORTIE RAPIDE)
            # ═══════════════════════════════════════════════════
            # Si UNIQUEMENT du texte (pas d'outils), c'est une réponse directe finale
            has_any_tool = False
            text_only_response = None
            
            for response_block in ia_responses:
                if isinstance(response_block, dict):
                    if "tool_output" in response_block:
                        has_any_tool = True
                        break
                    elif "text_output" in response_block:
                        # Garder le texte pour une sortie potentielle
                        text_block = response_block["text_output"]
                        if isinstance(text_block, dict) and "content" in text_block:
                            content = text_block["content"]
                            text_only_response = content.get('answer_text', str(content)) if isinstance(content, dict) else str(content)
                        elif isinstance(text_block, str):
                            text_only_response = text_block
            
            # Si UNIQUEMENT du texte (pas d'outils) → SORTIE IMMÉDIATE
            if not has_any_tool and text_only_response:
                logger.info(f"[PINNOKIO_WORKFLOW] ✅ Réponse texte simple détectée - sortie immédiate")
                return True, "TEXT_RESPONSE", text_only_response
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 5 : TRAITEMENT DES RÉPONSES (avec outils)
            # ═══════════════════════════════════════════════════
            for response_block in ia_responses:
                if not isinstance(response_block, dict):
                    next_user_input_parts.append(f"Réponse inattendue: {str(response_block)[:200]}")
                    continue
                
                # ───────────────────────────────────────────────
                # CAS 1 : TOOL_OUTPUT
                # ───────────────────────────────────────────────
                if "tool_output" in response_block:
                    tool_block = response_block["tool_output"]
                    tool_name = tool_block.get('tool_name', 'UnknownTool')
                    tool_content = tool_block.get('content', '')
                    
                    logger.info(f"  [PINNOKIO_WORKFLOW] Outil appelé: {tool_name}")
                    
                    # ▼▼▼ DÉTECTION TERMINATE_TASK ▼▼▼
                    if tool_name == 'TERMINATE_TASK':
                        if isinstance(tool_content, dict):
                            reason = tool_content.get('reason', 'Non spécifié')
                            conclusion = tool_content.get('conclusion', '')
                            tasks_completed = tool_content.get('tasks_completed', [])
                        else:
                            reason = "Terminaison demandée"
                            conclusion = str(tool_content)
                            tasks_completed = []
                        
                        logger.info(f"[PINNOKIO_WORKFLOW] ✓ TERMINATE_TASK - Raison: {reason}")
                        logger.info(f"[PINNOKIO_WORKFLOW] Tâches complétées: {tasks_completed}")
                        
                        # 🚪 SORTIE IMMÉDIATE
                        return True, "MISSION_COMPLETED", conclusion
                    
                    # ▼▼▼ DÉTECTION SPT ▼▼▼
                    elif tool_name in ['READ_FIREBASE_DOCUMENT', 'SEARCH_CHROMADB']:
                        logger.info(f"  [PINNOKIO_WORKFLOW] SPT exécuté: {tool_name}")
                        
                        if isinstance(tool_content, dict):
                            if tool_content.get('type') == 'success':
                                next_user_input_parts.append(
                                    f"✅ {tool_name} exécuté avec succès: {str(tool_content.get('data', ''))[:500]}"
                                )
                            elif tool_content.get('type') == 'error':
                                next_user_input_parts.append(
                                    f"❌ Erreur {tool_name}: {tool_content.get('message', 'Erreur inconnue')}"
                                )
                            else:
                                next_user_input_parts.append(f"Résultat {tool_name}: {str(tool_content)[:500]}")
                        else:
                            next_user_input_parts.append(f"Résultat {tool_name}: {str(tool_content)[:500]}")
                    
                    # ▼▼▼ DÉTECTION LPT ▼▼▼
                    elif tool_name in ['CALL_FILE_MANAGER_AGENT', 'CALL_ACCOUNTING_AGENT']:
                        logger.info(f"  [PINNOKIO_WORKFLOW] LPT démarré: {tool_name}")
                        
                        if isinstance(tool_content, dict) and tool_content.get('type') == 'lpt_started':
                            task_id = tool_content.get('task_id')
                            estimated_duration = tool_content.get('estimated_duration', 'inconnu')
                            message = tool_content.get('message', '')
                            
                            logger.info(f"  [PINNOKIO_WORKFLOW] Tâche LPT {task_id} démarrée (durée estimée: {estimated_duration})")
                            
                            next_user_input_parts.append(
                                f"⏳ Tâche asynchrone démarrée (ID: {task_id})\n"
                                f"Durée estimée: {estimated_duration}\n"
                                f"{message}\n"
                                f"Tu es maintenant DISPONIBLE pour répondre aux questions de l'utilisateur "
                                f"pendant l'exécution. Utilise les outils SPT si nécessaire."
                            )
                        else:
                            next_user_input_parts.append(f"Résultat {tool_name}: {str(tool_content)[:500]}")
                    
                    # Autres outils
                    else:
                        next_user_input_parts.append(f"Résultat outil {tool_name}: {str(tool_content)[:500]}")
                
                # ───────────────────────────────────────────────
                # CAS 2 : TEXT_OUTPUT
                # ───────────────────────────────────────────────
                elif "text_output" in response_block:
                    text_block = response_block["text_output"]
                    extracted_text = "Pas de texte"
                    
                    if isinstance(text_block, dict) and "content" in text_block:
                        content = text_block["content"]
                        if isinstance(content, dict):
                            extracted_text = content.get('answer_text', str(content))
                        else:
                            extracted_text = str(content)
                    elif isinstance(text_block, str):
                        extracted_text = text_block
                    
                    logger.debug(f"  [PINNOKIO_WORKFLOW] Texte: {extracted_text[:200]}...")
                    next_user_input_parts.append(f"Texte précédent: {extracted_text[:300]}")
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 6 : CALCULER TOKENS APRÈS LE TOUR
            # ═══════════════════════════════════════════════════
            try:
                tokens_after = manager_instance.agent.get_total_context_tokens(provider)
                turn_tokens = tokens_after - tokens_before
                
                logger.info(
                    f"[TOKENS] Tour {turn_count} terminé - "
                    f"Utilisé: +{turn_tokens:,} tokens | "
                    f"Total: {tokens_after:,}/{max_tokens_budget:,}"
                )
            except Exception as e:
                logger.warning(f"[TOKENS] Erreur calcul delta tokens: {e}")
            
            # ═══════════════════════════════════════════════════
            # ÉTAPE 7 : PRÉPARER INPUT POUR PROCHAIN TOUR
            # ═══════════════════════════════════════════════════
            if next_user_input_parts:
                user_input = "\n".join(next_user_input_parts)
            else:
                logger.warning("[PINNOKIO_WORKFLOW] Aucune réponse utilisable de l'IA")
                return False, "NO_IA_ACTION", "L'IA n'a pas fourni de réponse claire."
        
        # ═══════════════════════════════════════════════════
        # MAX TOURS ATTEINT
        # ═══════════════════════════════════════════════════
        logger.warning(f"[PINNOKIO_WORKFLOW] Maximum de {max_turns} tours atteint")
        
        # Vérifier s'il y a des LPT en cours
        active_lpt_count = manager_instance.get_active_lpt_count(thread_key)
        if active_lpt_count > 0:
            summary = f"Maximum de tours atteint mais {active_lpt_count} tâche(s) LPT en cours. Agent disponible pour l'utilisateur."
            logger.info(f"[PINNOKIO_WORKFLOW] {active_lpt_count} LPT en cours, agent disponible")
            return False, "LPT_IN_PROGRESS", summary
        
        # Aucun LPT en cours
        summary = f"Maximum de {max_turns} tours atteint. Dernier état: {user_input[:500]}"
        return False, "MAX_TURNS_REACHED", summary
        
    except Exception as e:
        import traceback
        logger.error(f"[PINNOKIO_WORKFLOW] ERREUR FATALE: {e}")
        traceback.print_exc()
        error_msg = f"Erreur dans pinnokio_agent_workflow: {str(e)}"
        return False, "ERROR_FATAL", error_msg

