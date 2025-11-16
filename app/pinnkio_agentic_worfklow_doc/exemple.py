########Agentic workflow example########

##la méthode workflow_agent_(nom et fonction de l'agent) est la méthode qui permet de lancer un workflow d'agent.
##doit etre crée dans la classe de BaseAIAgent, en s'assurant de choisir le provider  et le taille du model ( une liste des models est disponible)
## Ci dessous un exemple d'un agent qui accede a des notes de frais et peux travailler dessus.


def expenses_agent_workflow(self,
                                manager_instance: Any,
                                initial_query: str,
                                tools: List[Dict[str, Any]],
                                tool_mapping: Dict[str, Any],
                                size: ModelSize = ModelSize.SMALL,
                                provider: Optional[ModelProvider] = None,
                                max_tokens: int = 2048,
                                project_id: str = None,
                                job_id: str = None,
                                workflow_step: str = 'expenses_workflow',
                                max_turns: int = 7,
                                raw_output: bool = True) -> Tuple[bool, str, str]:
        """
        Workflow intelligent pour la recherche de notes de frais (expenses).
        Structure identique à ar_agent_workflow avec gestion de TERMINATE_SEARCH.
        
        Args:
            manager_instance: Instance de l'agent Expenses pour maintenir le contexte
            initial_query: Requête de l'utilisateur (peut contenir rapport précédent si itération > 1)
            tools: Liste des outils disponibles (GET_EXPENSES_INFO, VIEW_EXPENSE_DOCUMENT, SEARCH_IN_CHART_OF_ACCOUNT, TERMINATE_SEARCH)
            tool_mapping: Mapping des outils vers leurs fonctions
            size: Taille du modèle
            provider: Provider AI à utiliser
            max_tokens: Tokens maximum pour réponse
            project_id: ID du projet pour tracking
            job_id: ID du job pour tracking
            workflow_step: Nom de l'étape workflow
            max_turns: Nombre maximum de tours (défaut 7)
            raw_output: Si True, retourne format liste (défaut True)
            
        Returns:
            Tuple[bool, str, str]: (success, status_code, final_response_text)
        """
        try:
            print(f"\033[1;36m[EXPENSES_WORKFLOW] Démarrage - Tours max: {max_turns}\033[0m")
            
            turn_count = 0
            user_input = initial_query
            next_user_input_parts = []
            
            while turn_count < max_turns:
                turn_count += 1
                print(f"\033[96m[EXPENSES_WORKFLOW] Tour {turn_count}/{max_turns}\033[0m")
                
                # Appeler l'agent avec SON INSTANCE pour garder le contexte
                ia_responses = manager_instance.process_tool_use(
                    content=user_input,
                    tools=tools,
                    tool_mapping=tool_mapping,
                    size=size,
                    provider=provider,
                    max_tokens=max_tokens,
                    raw_output=raw_output
                )
                
                # Tracking des tokens
                if project_id and job_id:
                    manager_instance.load_token_usage_to_db(
                        project_id=project_id,
                        job_id=job_id,
                        workflow_step=f"{workflow_step}_turn_{turn_count}"
                    )
                
                print(f"\033[93m[EXPENSES_WORKFLOW] Réponse tour {turn_count}: {str(ia_responses)[:300]}...\033[0m")
                
                # Convertir en liste si nécessaire
                if not isinstance(ia_responses, list):
                    if ia_responses:
                        ia_responses = [ia_responses]
                    else:
                        ia_responses = []
                
                next_user_input_parts = []
                
                # Parcourir les réponses
                for response_block in ia_responses:
                    if not isinstance(response_block, dict):
                        next_user_input_parts.append(f"Réponse inattendue: {str(response_block)[:200]}")
                        continue
                    
                    # CAS 1: TOOL_OUTPUT
                    if "tool_output" in response_block:
                        tool_block = response_block["tool_output"]
                        tool_name = tool_block.get('tool_name', 'UnknownTool')
                        tool_content = tool_block.get('content', '')
                        
                        print(f"  [EXPENSES_WORKFLOW] Outil appelé: {tool_name}")
                        
                        # DÉTECTION TERMINATE_SEARCH
                        if tool_name == 'TERMINATE_SEARCH':
                            if isinstance(tool_content, dict):
                                reason = tool_content.get('reason', 'Non spécifié')
                                conclusion = tool_content.get('conclusion', '')
                                expense_job_id = tool_content.get('expense_job_id', '')
                            else:
                                reason = "Terminaison demandée"
                                conclusion = str(tool_content)
                                expense_job_id = ""
                            
                            print(f"\033[92m[EXPENSES_WORKFLOW] ✓ TERMINATE_SEARCH - Raison: {reason}\033[0m")
                            print(f"[EXPENSES_WORKFLOW] Expense Job ID: {expense_job_id}")
                            
                            # SORTIE IMMÉDIATE
                            return True, "MISSION_COMPLETED", conclusion
                        
                        # GET_EXPENSES_INFO
                        elif tool_name == 'GET_EXPENSES_INFO':
                            # Analyser le contenu
                            if isinstance(tool_content, dict):
                                if tool_content.get('type') == 'too_many_results':
                                    next_user_input_parts.append(
                                        f"Trop de résultats ({tool_content.get('count')} notes de frais). "
                                        f"Affine avec des filtres supplémentaires (montant, date, fournisseur)."
                                    )
                                elif tool_content.get('type') == 'expense_list':
                                    expenses = tool_content.get('expenses', [])
                                    next_user_input_parts.append(
                                        f"Liste de notes de frais trouvées: {expenses}. "
                                        f"Sélectionne la plus pertinente selon le montant et la date."
                                    )
                                else:
                                    next_user_input_parts.append(f"Résultat outil: {str(tool_content)[:500]}")
                            elif isinstance(tool_content, list):
                                next_user_input_parts.append(f"Notes de frais trouvées ({len(tool_content)}): {str(tool_content)[:500]}")
                            else:
                                next_user_input_parts.append(f"Résultat outil GET_EXPENSES_INFO: {str(tool_content)[:500]}")
                        
                        # VIEW_EXPENSE_DOCUMENT
                        elif tool_name == 'VIEW_EXPENSE_DOCUMENT':
                            print(f"  [EXPENSES_WORKFLOW] Résultat vision: {str(tool_content)[:200]}")
                            next_user_input_parts.append(f"Résultat de la vision du document: {str(tool_content)[:500]}")
                        
                        # SEARCH_IN_CHART_OF_ACCOUNT
                        elif tool_name == 'SEARCH_IN_CHART_OF_ACCOUNT':
                            print(f"  [EXPENSES_WORKFLOW] Compte comptable trouvé: {str(tool_content)[:200]}")
                            next_user_input_parts.append(f"Compte comptable: {str(tool_content)[:500]}")
                        
                        # Autres outils
                        else:
                            next_user_input_parts.append(f"Résultat outil {tool_name}: {str(tool_content)[:500]}")
                    
                    # CAS 2: TEXT_OUTPUT
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
                        
                        print(f"  [EXPENSES_WORKFLOW] Texte: {extracted_text[:200]}...")
                        next_user_input_parts.append(f"Texte précédent: {extracted_text[:300]}")
                
                # Préparer input pour prochain tour
                if next_user_input_parts:
                    user_input = "\n".join(next_user_input_parts)
                else:
                    # Aucune réponse utilisable
                    print("\033[91m[EXPENSES_WORKFLOW] Aucune réponse utilisable de l'IA\033[0m")
                    return False, "NO_IA_ACTION", "L'IA n'a pas fourni de réponse claire."
            
            # Max tours atteint
            print(f"\033[91m[EXPENSES_WORKFLOW] Maximum de {max_turns} tours atteint\033[0m")
            
            # Générer un rapport de ce qui s'est passé
            summary = f"Maximum de {max_turns} tours atteint. Dernier état: {user_input[:500]}"
            
            return False, "MAX_TURNS_REACHED", summary
            
        except Exception as e:
            import traceback
            print(f"\033[91m[EXPENSES_WORKFLOW] ERREUR FATALE: {e}\033[0m")
            traceback.print_exc()
            error_msg = f"Erreur dans expenses_agent_workflow: {str(e)}"
            return False, "ERROR_FATAL", error_msg


##Extrait dans l'application qui fait appel a l'agent 
## BaseAiGent est initialisé au niveau de l'application ainsi que les prompt et la définition des outils

def OPEN_EXPENSES_CHECK(self, query):
        """
        Recherche intelligente de notes de frais avec workflow itératif.
        Utilise expenses_agent_workflow pour des recherches avancées avec structure agentic.
        Inclut outil de vision pour vérifier les documents en cas de doute.
        """
        print(f"\033[1;32mDÉMARRAGE DU CONTRÔLE DE NOTES DE FRAIS (AGENTIC).....\033[0m")
        self.EXPENSES_MANAGER_INIT()
        
        # TOOL SET ÉTENDU avec recherche, vision et TERMINATE_SEARCH
        tool_set = [
            {
                "name": "GET_EXPENSES_INFO",
                "description": "Filtre et retourne les notes de frais selon différents critères. Supporte la recherche partielle par fournisseur et les plages de montants/dates.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "supplier_name": {
                            "type": "string", 
                            "description": "Nom du fournisseur (recherche partielle supportée - insensible à la casse)."
                        },
                        "job_id": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "description": "Liste des identifiants de notes de frais à rechercher."
                        },
                        "payment_method": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "description": "Méthode de paiement pour filtrer. Exemples : ['CARD', 'CASH', 'TRANSFER']."
                        },
                        "status": {
                            "type": "array", 
                            "items": {
                                "type": "string"
                            },
                            "description": "État des notes de frais pour filtrer. Exemples : ['to_process', 'processed']."
                        },
                        "date_range": {
                            "type": "object",
                            "properties": {
                                "start_date": {"type": "string", "description": "Date de début (format YYYY-MM-DD)"},
                                "end_date": {"type": "string", "description": "Date de fin (format YYYY-MM-DD)"}
                            },
                            "description": "Plage de dates pour filtrer les notes de frais."
                        },
                        "amount_range": {
                            "type": "object", 
                            "properties": {
                                "min_amount": {"type": "number", "description": "Montant minimum"},
                                "max_amount": {"type": "number", "description": "Montant maximum"}
                            },
                            "description": "Plage de montants pour filtrer les notes de frais. Utilisez pour des montants approximatifs."
                        }
                    },
                    "required": []
                }
            },
            {
                "name": "VIEW_EXPENSE_DOCUMENT",
                "description": "🔍 Visualiser le document justificatif d'une note de frais pour vérifier les détails (montant, fournisseur, date). À utiliser EN CAS DE DOUTE uniquement, par exemple quand le montant et la date correspondent mais le nom du fournisseur semble différent.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "expense_job_id": {
                            "type": "string",
                            "description": "L'identifiant unique (job_id) de la note de frais dont vous voulez visualiser le document"
                        },
                        "question": {
                            "type": "string",
                            "description": "La question spécifique que vous souhaitez poser sur le document (ex: 'Quel est le montant exact et la devise?', 'Quel est le nom du fournisseur visible sur le document?')"
                        }
                    },
                    "required": ["expense_job_id", "question"]
                }
            },
            {
                "name": "SEARCH_IN_CHART_OF_ACCOUNT",
                "description": "🔍 Rechercher le compte comptable approprié pour imputer une charge. À utiliser une fois la note de frais identifiée pour obtenir le numéro de compte comptable.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Question contextuelle pour trouver le compte approprié. Ex: 'Quel compte de charge utiliser pour des frais de restauration pour [Nom société]?' ou 'Quel compte pour des frais de déplacement (parking, essence)?'"
                        }
                    },
                    "required": ["question"]
                }
            },
            {
                "name": "TERMINATE_SEARCH",
                "description": "🎯 Terminer la recherche quand la mission est accomplie. Utilisez cet outil dès que vous avez identifié la note de frais correspondante ET obtenu toutes les informations nécessaires (compte comptable inclus) OU conclu qu'aucune note de frais ne correspond. Ne continuez pas à itérer inutilement !",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Raison de la terminaison : 'Note de frais trouvée et identifiée' / 'Aucune correspondance trouvée' / 'Mission accomplie'"
                        },
                        "expense_job_id": {
                            "type": "string",
                            "description": "ID de la note de frais identifiée (si applicable). Laisser vide si aucune note trouvée."
                        },
                        "conclusion": {
                            "type": "string",
                            "description": "Votre réponse finale COMPLÈTE et DÉTAILLÉE pour l'utilisateur. DOIT inclure: job_id, nature, date, montant, devise, libellé comptable, bank_case='no_counterpart', entry_type='expense_entry', odoo_final_account_number, odoo_vat_type, odoo_vat_percentages."
                        }
                    },
                    "required": ["reason", "conclusion"]
                }
            }
        ]
        
        # Tool mapping avec les trois outils
        tool_map = {
            "GET_EXPENSES_INFO": self.filter_expenses_by_criteria,
            "VIEW_EXPENSE_DOCUMENT": self.VIEW_EXPENSE_DOCUMENT,
            "SEARCH_IN_CHART_OF_ACCOUNT": self.FETCH_ACCOUNT
        }
        
        # Obtenir résumé des expenses disponibles
        available_expenses_summary = self.get_expenses_summary()
        
        # Préparer la requête avec contexte enrichi (PREMIER MESSAGE)
        query_enriched = f"""{query}
        
*************************************************************************
CONTEXTE DES NOTES DE FRAIS DISPONIBLES :
{available_expenses_summary}

*************************************************************************

ASTUCES IMPORTANTES POUR LE MATCHING :
- ⚠️ PRIORITÉ ABSOLUE : Montant et devise (tolérance ±0.01)
- Les noms de fournisseurs peuvent TRÈS DIFFÉRENTS du libellé bancaire
- Exemples : "PAYPAL *AMAZON" vs "Amazon", "SQ *CAFE" vs nom complet du café
- Sur les paiements POS/carte, le nom peut être cryptique ou abrégé
- Utilisez amount_range pour montants approximatifs (±2 unités)
- Utilisez date_range pour dates avec tolérance (±3 jours)
- EN CAS DE DOUTE sur le fournisseur : utilisez VIEW_EXPENSE_DOCUMENT
- Une fois la note identifiée : utilisez SEARCH_IN_CHART_OF_ACCOUNT pour le compte
- Itérez autant que nécessaire pour trouver LA note de frais correspondante
"""
        
        # ================================================================
        # BOUCLE EXTERNE : ITÉRATIONS AVEC RAPPORTS
        # ================================================================
        max_iterations = 3
        iteration = 0
        current_input = query_enriched  # Premier message complet
        
        while iteration < max_iterations:
            iteration += 1
            print(f"\033[1;35m[OPEN_EXPENSES_CHECK] Itération {iteration}/{max_iterations}\033[0m")
            
            # APPEL DU WORKFLOW INTELLIGENT (boucle interne de tours)
            success, status_code, report = self.expenses_manager.expenses_agent_workflow(
                manager_instance=self.expenses_manager,  # Passer l'instance pour le contexte
                initial_query=current_input,
                tools=tool_set,
                tool_mapping=tool_map,
                size=ModelSize.SMALL,
                project_id=self.collection_id,
                job_id=self.sp_k,
                workflow_step=f'open_expenses_check_iter_{iteration}',
                max_turns=7,
                raw_output=True
            )
            
            print(f"\033[92m[OPEN_EXPENSES_CHECK] Itération {iteration} - Status: {status_code}\033[0m")
            print(f"[OPEN_EXPENSES_CHECK] Report: {report[:300]}...")
            
            # Vérifier si la mission est accomplie
            if status_code == "MISSION_COMPLETED":
                print(f"\033[1;32m[OPEN_EXPENSES_CHECK] ✓ Mission accomplie à l'itération {iteration}\033[0m")
                self.audit.add_messages_ai_hu(f"Réponse du département Expenses: {report}")
                self.expenses_manager.flush_chat_history()
                return report
            
            # Si pas terminé, préparer le prochain input avec le rapport
            if iteration < max_iterations:
                current_input = f"""╔═══════════════════════════════════════════════════════════╗
║              RAPPORT DE L'ITÉRATION PRÉCÉDENTE            ║
╚═══════════════════════════════════════════════════════════╝

{report}

╔═══════════════════════════════════════════════════════════╗
║              RAPPEL DE LA MISSION INITIALE                ║
╚═══════════════════════════════════════════════════════════╝

{query_enriched}

╔═══════════════════════════════════════════════════════════╗
║                      INSTRUCTIONS                         ║
╚═══════════════════════════════════════════════════════════╝

Tu as {max_iterations - iteration} itération(s) restante(s).
Continue ta recherche OU utilise TERMINATE_SEARCH si tu as trouvé la note de frais.
⚠️ RAPPEL : Si le montant et la date correspondent mais le nom diffère, utilise VIEW_EXPENSE_DOCUMENT !
"""
        
        # Maximum d'itérations atteint
        print(f"\033[91m[OPEN_EXPENSES_CHECK] Maximum d'itérations atteint ({max_iterations})\033[0m")
        self.audit.add_messages_ai_hu(f"Réponse du département Expenses (max itérations): {report}")
        self.expenses_manager.flush_chat_history()
        
        return report
    