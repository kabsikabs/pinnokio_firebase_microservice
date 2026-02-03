import re
from typing import Dict, List, Optional, Union

class TextUpdaterAgent:
    """
    Agent de manipulation de texte.
    La logique LLM est gérée en dehors de cette classe.
    Cette classe contient uniquement les méthodes d'application des opérations de texte.
    """
    
    def __init__(self, collection_name=None, firebase_user_id=None, dms_system=None, space_manager=None):
        # Initialisation des attributs (pour compatibilité)
        self.collection_name = collection_name
        self.firebase_user_id = firebase_user_id
        self.dms_system = dms_system
        self.space_manager = space_manager
    
    def _parse_text_into_sections(self, text: str, max_sections: int = 20) -> List[str]:
        """
        Parse le texte en sections (paragraphes) pour faciliter la référence.
        
        Args:
            text: Texte à parser
            max_sections: Nombre maximum de sections à retourner
        
        Returns:
            Liste des sections (paragraphes)
        """
        if not text:
            return []
        
        # Séparer par double saut de ligne (paragraphes)
        sections = re.split(r'\n\s*\n', text.strip())
        
        # Si pas de paragraphes, séparer par saut de ligne simple
        if len(sections) == 1:
            sections = text.split('\n')
        
        # Filtrer les sections vides et limiter
        sections = [s.strip() for s in sections if s.strip()][:max_sections]
        
        return sections
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 🆕 NOUVELLE APPROCHE : Modification par ancres (anchor_before / anchor_after)
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _find_target_by_anchors(self, text: str, anchor_before: str = None, anchor_after: str = None) -> Dict:
        """
        Trouve la position exacte de la zone cible en utilisant les ancres.
        
        Les ancres fonctionnent comme des coordonnées :
        - anchor_before : texte qui PRÉCÈDE la zone cible (la zone commence APRÈS)
        - anchor_after : texte qui SUIT la zone cible (la zone finit AVANT)
        
        Args:
            text: Texte complet
            anchor_before: Texte avant la zone cible (None = début du texte)
            anchor_after: Texte après la zone cible (None = fin du texte)
        
        Returns:
            Dict avec start, end, target_text ou error
        """
        start_pos = 0
        end_pos = len(text)
        
        # Trouver la position après anchor_before
        if anchor_before:
            pos = text.find(anchor_before)
            if pos == -1:
                # Essayer recherche insensible à la casse
                pos = text.lower().find(anchor_before.lower())
                if pos != -1:
                    # Utiliser la position trouvée
                    anchor_before = text[pos:pos + len(anchor_before)]
                else:
                    return {
                        "success": False,
                        "error": f"anchor_before '{anchor_before[:30]}...' non trouvé dans le texte",
                        "hint": "Vérifiez que les 12+ caractères correspondent exactement au texte"
                    }
            start_pos = pos + len(anchor_before)
        
        # Trouver la position de anchor_after (chercher après start_pos)
        if anchor_after:
            pos = text.find(anchor_after, start_pos)
            if pos == -1:
                # Essayer recherche insensible à la casse
                pos = text.lower().find(anchor_after.lower(), start_pos)
                if pos != -1:
                    anchor_after = text[pos:pos + len(anchor_after)]
                else:
                    return {
                        "success": False,
                        "error": f"anchor_after '{anchor_after[:30]}...' non trouvé après anchor_before",
                        "hint": "Vérifiez que anchor_after existe APRÈS anchor_before dans le texte"
                    }
            end_pos = pos
        
        # Extraire le texte cible
        target_text = text[start_pos:end_pos]
        
        return {
            "success": True,
            "start": start_pos,
            "end": end_pos,
            "target_text": target_text,
            "anchor_before_used": anchor_before,
            "anchor_after_used": anchor_after
        }
    
    def apply_operation_with_anchors(self, text: str, operation: str, 
                                      anchor_before: str = None, 
                                      anchor_after: str = None,
                                      new_content: str = "") -> Dict:
        """
        Applique une opération en utilisant les ancres pour localiser la zone cible.
        
        🎯 PRINCIPE DES ANCRES :
        - anchor_before : 12+ caractères QUI PRÉCÈDENT la zone à modifier
        - anchor_after : 12+ caractères QUI SUIVENT la zone à modifier
        - La zone cible est ENTRE les deux ancres
        
        📋 CAS D'UTILISATION :
        | anchor_before | anchor_after | Zone ciblée                    |
        |---------------|--------------|--------------------------------|
        | None          | présent      | Du DÉBUT jusqu'à anchor_after  |
        | présent       | None         | De anchor_before jusqu'à la FIN|
        | présent       | présent      | ENTRE les deux ancres          |
        | None          | None         | TOUT le texte                  |
        
        Args:
            text: Texte à modifier
            operation: "add", "replace", ou "delete"
            anchor_before: Texte précédant la zone cible (None = début)
            anchor_after: Texte suivant la zone cible (None = fin)
            new_content: Nouveau contenu (pour add/replace)
        
        Returns:
            Dict avec success, updated_text, et détails de l'opération
        """
        # Validation
        if operation not in ["add", "replace", "delete"]:
            return {
                "success": False,
                "error": f"Opération inconnue: {operation}. Utilisez 'add', 'replace', ou 'delete'",
                "updated_text": text
            }
        
        # Cas spécial : add au début/fin sans ancres
        if operation == "add":
            if anchor_before is None and anchor_after is None:
                # Ajouter au début par défaut
                return {
                    "success": True,
                    "updated_text": new_content + text,
                    "operation_details": {
                        "operation": "add",
                        "position": "beginning",
                        "added_content": new_content
                    }
                }
            elif anchor_before and anchor_after is None:
                # Ajouter après anchor_before
                result = self._find_target_by_anchors(text, anchor_before, None)
                if not result.get("success"):
                    result["updated_text"] = text
                    return result
                
                insert_pos = result["start"]
                updated_text = text[:insert_pos] + new_content + text[insert_pos:]
                return {
                    "success": True,
                    "updated_text": updated_text,
                    "operation_details": {
                        "operation": "add",
                        "position": insert_pos,
                        "after_anchor": anchor_before,
                        "added_content": new_content
                    }
                }
            elif anchor_before is None and anchor_after:
                # Ajouter avant anchor_after
                result = self._find_target_by_anchors(text, None, anchor_after)
                if not result.get("success"):
                    result["updated_text"] = text
                    return result
                
                insert_pos = result["end"]
                updated_text = text[:insert_pos] + new_content + text[insert_pos:]
                return {
                    "success": True,
                    "updated_text": updated_text,
                    "operation_details": {
                        "operation": "add",
                        "position": insert_pos,
                        "before_anchor": anchor_after,
                        "added_content": new_content
                    }
                }
        
        # Trouver la zone cible avec les ancres
        result = self._find_target_by_anchors(text, anchor_before, anchor_after)
        
        if not result.get("success"):
            result["updated_text"] = text
            return result
        
        start = result["start"]
        end = result["end"]
        target_text = result["target_text"]
        
        # Appliquer l'opération
        if operation == "add":
            # Ajouter entre les ancres (après le texte existant)
            updated_text = text[:end] + new_content + text[end:]
            return {
                "success": True,
                "updated_text": updated_text,
                "operation_details": {
                    "operation": "add",
                    "position": end,
                    "anchor_before": anchor_before,
                    "anchor_after": anchor_after,
                    "added_content": new_content
                }
            }
        
        elif operation == "replace":
            # Remplacer le contenu entre les ancres
            updated_text = text[:start] + new_content + text[end:]
            return {
                "success": True,
                "updated_text": updated_text,
                "operation_details": {
                    "operation": "replace",
                    "start": start,
                    "end": end,
                    "replaced_text": target_text,
                    "new_content": new_content,
                    "anchor_before": anchor_before,
                    "anchor_after": anchor_after
                }
            }
        
        elif operation == "delete":
            # Supprimer le contenu entre les ancres
            updated_text = text[:start] + text[end:]
            return {
                "success": True,
                "updated_text": updated_text,
                "operation_details": {
                    "operation": "delete",
                    "start": start,
                    "end": end,
                    "deleted_text": target_text,
                    "anchor_before": anchor_before,
                    "anchor_after": anchor_after
                }
            }
    
    def apply_operations_v2(self, text_to_update: str, operations_list: List[Dict]) -> Dict:
        """
        🆕 VERSION 2 : Applique des opérations avec le système d'ancres.
        
        Chaque opération peut contenir :
        - operation: "add", "replace", "delete"
        - anchor_before: Texte précédant la zone (12+ caractères recommandés)
        - anchor_after: Texte suivant la zone (12+ caractères recommandés)
        - new_content: Nouveau contenu (pour add/replace)
        
        Args:
            text_to_update: Texte original
            operations_list: Liste d'opérations avec ancres
        
        Returns:
            Dict avec success, updated_text, operations_log
        """
        if not isinstance(operations_list, list):
            return {
                "success": False,
                "updated_text": text_to_update,
                "operations_log": [],
                "error": "operations_list doit être une liste"
            }
        
        current_text = str(text_to_update)
        operations_log = []
        final_success = True
        
        for i, op in enumerate(operations_list):
            if not isinstance(op, dict):
                log_entry = {
                    "op_index": i,
                    "success": False,
                    "error": "L'opération doit être un dictionnaire"
                }
                operations_log.append(log_entry)
                final_success = False
                break
            
            operation = op.get("operation")
            anchor_before = op.get("anchor_before")
            anchor_after = op.get("anchor_after")
            new_content = op.get("new_content", "")
            
            # Validation
            if not operation:
                log_entry = {
                    "op_index": i,
                    "success": False,
                    "error": "Le champ 'operation' est requis"
                }
                operations_log.append(log_entry)
                final_success = False
                break
            
            # Pour replace/delete entre deux points, les deux ancres sont requises
            if operation in ["replace", "delete"]:
                if anchor_before is None and anchor_after is None:
                    log_entry = {
                        "op_index": i,
                        "success": False,
                        "error": f"Pour '{operation}', au moins une ancre (anchor_before ou anchor_after) est requise"
                    }
                    operations_log.append(log_entry)
                    final_success = False
                    break
            
            print(f"Opération {i+1}/{len(operations_list)}: {operation}")
            print(f"  anchor_before: {anchor_before[:30] if anchor_before else 'None'}...")
            print(f"  anchor_after: {anchor_after[:30] if anchor_after else 'None'}...")
            
            # Appliquer l'opération
            result = self.apply_operation_with_anchors(
                text=current_text,
                operation=operation,
                anchor_before=anchor_before,
                anchor_after=anchor_after,
                new_content=new_content
            )
            
            log_entry = {
                "op_index": i,
                "operation": operation,
                "anchor_before": anchor_before,
                "anchor_after": anchor_after,
                **result
            }
            
            if result.get("success"):
                current_text = result["updated_text"]
                print(f"  -> ✅ Succès")
            else:
                print(f"  -> ❌ Échec: {result.get('error')}")
                final_success = False
                operations_log.append(log_entry)
                break
            
            operations_log.append(log_entry)
        
        return {
            "success": final_success,
            "updated_text": current_text,
            "operations_log": operations_log,
            "error": None if final_success else "Une ou plusieurs opérations ont échoué"
        }

    def apply_operations(self, text_to_update, operations_list):
        """
        Applique une liste d'opérations de mise à jour sur un texte.
        
        Args:
            text_to_update (str): Texte à mettre à jour
            operations_list (list): Liste d'opérations générées par le LLM
                Chaque opération contient: section_type, operation, new_content, context (optionnel)
            
        Returns:
            dict: Résultat de l'opération de mise à jour avec:
                - success (bool): Succès ou échec
                - updated_text (str): Texte mis à jour
                - operations_log (list): Log détaillé de chaque opération
                - error (str): Message d'erreur si échec
        """
        if not isinstance(operations_list, list):
            error_msg = "operations_list doit être une liste."
            return {"success": False, "updated_text": text_to_update, "operations_log": [], "error": error_msg}


        current_text_being_updated = str(text_to_update) # Copie modifiable
        operations_log = []
        final_success = True

        for i, op_args in enumerate(operations_list):
            if not isinstance(op_args, dict):
                log_entry = {"op_index": i, "success": False, "error": "Argument d'opération n'est pas un dictionnaire.", "args": op_args}
                operations_log.append(log_entry)
                print(log_entry["error"])
                final_success = False
                break # Arrêter si une opération est mal formée

            # Extraire les arguments pour _update_text_section
            section_type = op_args.get("section_type")
            new_content = op_args.get("new_content")
            operation_type = op_args.get("operation") # Renommé pour éviter confusion avec la variable 'operation'
            context_from_llm = op_args.get("context")

            log_entry = {"op_index": i, "args_from_llm": op_args, "text_before_op": str(current_text_being_updated)} # Copie pour le log

            if not all([section_type, new_content is not None, operation_type]): # new_content peut être ""
                log_entry.update({"success": False, "error": "Arguments manquants (section_type, new_content, ou operation) dans l'opération proposée par le LLM."})
                operations_log.append(log_entry)
                print(log_entry["error"])
                final_success = False
                break # Arrêter en cas d'arguments manquants

            print(f"Opération {i+1}/{len(operations_list)}: type='{operation_type}', section='{section_type}', context='{context_from_llm}'")
            
            try:
                single_op_result = self._update_text_section(
                    text=current_text_being_updated,
                    section_type=section_type,
                    new_content=new_content,
                    operation=operation_type, # Utiliser operation_type
                    context=context_from_llm
                )
                log_entry.update(single_op_result) # Ajoute les détails du résultat de _update_text_section

                if single_op_result.get("success"):
                    current_text_being_updated = single_op_result["updated_text"]
                    print(f"  -> Succès. Nouveau texte (partiel):\n{current_text_being_updated[:200]}...")
                else:
                    print(f"  -> Échec: {single_op_result.get('error')}")
                    final_success = False
                    # On peut choisir d'arrêter à la première erreur ou de logger et continuer
                    # Pour l'instant, on arrête pour éviter des modifications basées sur un état incorrect.
                    operations_log.append(log_entry)
                    break 
                operations_log.append(log_entry)

            except Exception as e_call:
                error_detail = f"Erreur Python inattendue lors de l'appel de _update_text_section: {str(e_call)}"
                log_entry.update({"success": False, "error": error_detail})
                operations_log.append(log_entry)
                print(error_detail)
                import traceback
                traceback.print_exc()
                final_success = False
                break # Arrêter en cas d'exception Python

        return {
            "success": final_success,
            "updated_text": current_text_being_updated,
            "operations_log": operations_log,
            "error": None if final_success else "Une ou plusieurs opérations de mise à jour ont échoué."
        }

    def update_ai_book_json(self, ai_book_json, operations):
        """
        Applique des opérations CRUD sur le JSON du livre IA.
        operations: liste d'objets {action, path, index, value}
        - path: chemin par points ex: "supplier_specific_rules.STRIPE.rules"
        - index: identifiant unique de l'élément à créer/modifier/supprimer (si applicable)
        """
        import copy
        from typing import Any, Dict

        def get_path_ref(root: Dict[str, Any], path: str):
            node = root
            if not path:
                return node
            for key in path.split('.'):
                if key not in node or not isinstance(node[key], dict):
                    node[key] = {}
                node = node[key]
            return node

        result = copy.deepcopy(ai_book_json) if isinstance(ai_book_json, dict) else {}
        logs = []
        success = True

        for op in operations or []:
            action = op.get('action')
            path = op.get('path', '')
            index = op.get('index')
            value = op.get('value')
            entry = {"action": action, "path": path, "index": index}
            try:
                target = get_path_ref(result, path)
                if action == 'CREATE':
                    if index is None:
                        raise ValueError("index requis pour CREATE")
                    if not isinstance(target, dict):
                        raise ValueError("La cible du path doit être un objet pour CREATE")
                    if index in target:
                        raise ValueError("Index déjà existant")
                    target[index] = value
                elif action == 'READ':
                    entry['value'] = target.get(index) if isinstance(target, dict) and index else target
                elif action == 'UPDATE':
                    if index is None:
                        raise ValueError("index requis pour UPDATE")
                    if not isinstance(target, dict) or index not in target:
                        raise ValueError("Index introuvable pour UPDATE")
                    target[index] = value
                elif action == 'DELETE':
                    if index is None:
                        raise ValueError("index requis pour DELETE")
                    if not isinstance(target, dict) or index not in target:
                        raise ValueError("Index introuvable pour DELETE")
                    del target[index]
                else:
                    raise ValueError(f"Action inconnue: {action}")
                entry['success'] = True
            except Exception as e:
                entry['success'] = False
                entry['error'] = str(e)
                success = False
            logs.append(entry)

        return {"success": success, "updated_json": result, "operations_log": logs}


    def _update_text_section(self, text, section_type, new_content, operation, context=None):
        """
        Implémentation de l'outil de mise à jour de texte avec regex.
        (Votre code existant pour _update_text_section reste ici, inchangé, mais assurez-vous qu'il est robuste)
        """
        import re
        updated_text = str(text) # Travailler sur une copie
        regex_pattern_used = None
        replacement_string_used = None
        
        try:
            if section_type == "beg":
                if operation == "add":
                    regex_pattern_used = r"^"
                    replacement_string_used = str(new_content) # S'assurer que c'est une chaîne
                    updated_text = replacement_string_used + updated_text
                elif operation == "replace":
                    # Remplacer le début. Ici, 'context' pourrait être utilisé pour définir quelle partie du début.
                    # Ou, si context est None, on pourrait remplacer une portion basée sur la longueur de new_content.
                    # Pour simplifier, si context est donné, on l'utilise comme ce qui doit être au début.
                    if context:
                        if text.startswith(context):
                            regex_pattern_used = f"^{re.escape(context)}"
                            replacement_string_used = str(new_content)
                            updated_text = re.sub(regex_pattern_used, replacement_string_used, text, 1)
                        else:
                            return {"success": False, "error": f"Début du texte ne correspond pas au contexte '{context}' pour remplacement.", "updated_text": text}
                    else: # Remplacer les N premiers caractères, N = len(new_content)
                        len_replace = len(str(new_content))
                        updated_text = str(new_content) + text[len_replace:]
                elif operation == "delete":
                    # 'context' ici est ce qu'il faut supprimer au début.
                    if context:
                        if text.startswith(context):
                            regex_pattern_used = f"^{re.escape(context)}"
                            replacement_string_used = ""
                            updated_text = re.sub(regex_pattern_used, replacement_string_used, text, 1)
                        else:
                            return {"success": False, "error": f"Début du texte ne correspond pas au contexte '{context}' pour suppression.", "updated_text": text}
                    else: # Si pas de contexte, 'new_content' pourrait indiquer le nb de char à supprimer (non implémenté ici)
                         return {"success": False, "error": "Pour 'delete' au début, veuillez fournir un 'context' à supprimer.", "updated_text": text}


            elif section_type == "end":
                if operation == "add":
                    regex_pattern_used = r"$"
                    replacement_string_used = str(new_content)
                    updated_text = updated_text + replacement_string_used
                elif operation == "replace":
                    if context:
                        if text.endswith(context):
                            # Échapper le contexte pour regex et s'assurer qu'il ancre à la fin
                            regex_pattern_used = f"{re.escape(context)}$"
                            replacement_string_used = str(new_content)
                            updated_text = re.sub(regex_pattern_used, replacement_string_used, text, 1)
                        else:
                             return {"success": False, "error": f"Fin du texte ne correspond pas au contexte '{context}' pour remplacement.", "updated_text": text}
                    else: # Remplacer les N derniers caractères
                        len_replace = len(str(new_content))
                        updated_text = text[:-len_replace] + str(new_content)
                elif operation == "delete":
                    if context:
                        if text.endswith(context):
                            regex_pattern_used = f"{re.escape(context)}$"
                            replacement_string_used = ""
                            updated_text = re.sub(regex_pattern_used, replacement_string_used, text, 1)
                        else:
                            return {"success": False, "error": f"Fin du texte ne correspond pas au contexte '{context}' pour suppression.", "updated_text": text}
                    else:
                        return {"success": False, "error": "Pour 'delete' à la fin, veuillez fournir un 'context' à supprimer.", "updated_text": text}
            
            elif section_type == "mid":
                if not context:
                    return {"success": False, "error": "Contexte requis pour section_type 'mid'", "updated_text": text}
                
                # 🆕 NOUVELLE APPROCHE : Recherche intelligente du contexte
                # 1. Essayer recherche exacte d'abord
                escaped_context = re.escape(context)
                match_found = re.search(escaped_context, text)
                
                # 2. Si pas trouvé, essayer recherche flexible (ignorer espaces multiples, casse)
                if not match_found:
                    # Normaliser : espaces multiples → un seul espace, ignorer casse
                    normalized_context = re.sub(r'\s+', ' ', context.strip())
                    normalized_text = re.sub(r'\s+', ' ', text)
                    
                    # Recherche insensible à la casse
                    pattern = re.escape(normalized_context)
                    match_found = re.search(pattern, normalized_text, re.IGNORECASE)
                    
                    if match_found:
                        # Trouvé avec normalisation, utiliser le texte original pour la correspondance
                        # Chercher dans le texte original avec une recherche plus flexible
                        flexible_pattern = re.escape(context).replace(r'\ ', r'\s+')
                        match_found = re.search(flexible_pattern, text, re.IGNORECASE)
                        if match_found:
                            # Utiliser le match trouvé
                            context = text[match_found.start():match_found.end()]
                            escaped_context = re.escape(context)
                
                # 3. Si toujours pas trouvé, essayer recherche par mots-clés (premiers mots du contexte)
                if not match_found:
                    # Extraire les premiers mots-clés (3-5 premiers mots)
                    keywords = context.split()[:5]
                    if len(keywords) >= 2:
                        keyword_pattern = '.*?'.join([re.escape(kw) for kw in keywords])
                        match_found = re.search(keyword_pattern, text, re.IGNORECASE)
                        if match_found:
                            # Utiliser une portion plus large autour des mots-clés trouvés
                            start = max(0, match_found.start() - 20)
                            end = min(len(text), match_found.end() + 20)
                            context = text[start:end]
                            escaped_context = re.escape(context)
                
                # 4. Si toujours pas trouvé, retourner erreur avec suggestions
                if not match_found:
                    context_preview = context[:50] + "..." if len(context) > 50 else context
                    # Parser le texte en sections pour aider le LLM
                    sections = self._parse_text_into_sections(text)
                    sections_preview = "\n".join([f"  [{i}] {s[:60]}..." for i, s in enumerate(sections[:5])])
                    
                    return {
                        "success": False,
                        "error": (
                            f"Contexte '{context_preview}' non trouvé dans le texte. "
                            f"💡 ASTUCE : Le système a essayé une recherche flexible mais n'a rien trouvé. "
                            f"Essayez d'utiliser les premiers mots-clés de la section que vous voulez modifier, "
                            f"ou référez-vous aux sections disponibles ci-dessous."
                        ),
                        "updated_text": text,
                        "hint": "Utilisez les premiers mots-clés de la section à modifier plutôt que le texte complet.",
                        "text_sections_preview": sections_preview if sections else None,
                        "total_sections": len(sections) if sections else 0
                    }

                if operation == "add":
                    # Ajoute new_content APRÈS la première occurrence du contexte
                    regex_pattern_used = f"({escaped_context})" # Capturer le contexte pour le réinsérer
                    replacement_string_used = r"\1" + str(new_content)
                    updated_text = re.sub(regex_pattern_used, replacement_string_used, text, 1)
                elif operation == "replace":
                    regex_pattern_used = escaped_context
                    replacement_string_used = str(new_content)
                    updated_text = re.sub(regex_pattern_used, replacement_string_used, text, 1)
                elif operation == "delete":
                    regex_pattern_used = escaped_context
                    replacement_string_used = ""
                    updated_text = re.sub(regex_pattern_used, replacement_string_used, text, 1)
            
            else:
                return {"success": False, "error": f"Type de section inconnu: {section_type}", "updated_text": text}

        except re.error as e_re:
            return {"success": False, "error": f"Erreur Regex: {str(e_re)}", "updated_text": text, "regex_pattern_used": regex_pattern_used}
        except Exception as e_gen:
            return {"success": False, "error": f"Erreur générale dans _update_text_section: {str(e_gen)}", "updated_text": text}
        
        return {
            "success": True,
            "updated_text": updated_text,
            # Vous pouvez ajouter plus de détails sur l'opération effectuée ici si nécessaire pour le log
            "operation_details": {
                "operation": operation,
                "section_type": section_type,
                "context_used": context,
                "new_content_provided": new_content,
                "regex_pattern_used": regex_pattern_used,
                "replacement_string_used": replacement_string_used
            }
        }


# ============================================================================
# EXEMPLES ET TESTS
# ============================================================================

# Créer l'agent
#text_updater = TextUpdaterAgent()

# Exemple de texte à mettre à jour
original_text = """La société Emerald Stay S.A est une société dans le domaine de la paro-hôtellerie.
La société Emerald S.A. se trouvant à Genève est la société holding détenant les sociétés opérationnelles 
qui sont principalement Emerald S.A.S. en France, Emerald Espagne et Emerald Stay au Maroc. La société Emerald Stay 
suisse S.A.S. s'occupe principalement de faire la gestion des autres sociétés du groupe et refacture à ces sociétés des frais de management fees. 
Donc nous nous attendons d'avoir beaucoup de charges dans ces sociétés qui seront des frais de consultants, d'honoraires, d'avocats, divers tiers. 
Nous aurons aussi des business analysts, divers frais d'administration, frais de comptabilité. Nous avons aussi une employée qui s'appelle Fanny Chapelle qui est résidente ici à Genève. 
Donc c'est la seule masse salariale se trouvant dans la société Emerald Stay S.A. Suisse. 
Le bilan de Emerald Stay S.A. est composé particulièrement de participations élevées qui ont été acquises récemment, 
qui sont Ski Verbier pour un montant de 6.169.000. 
Et les autres participations qui sont des participations du groupe de Emerald Stay E.S. 
France Morzine qui est Emerald Stay France, Emerald Stay E.S. Barcelone qui est Emerald Stay Espagne et Emerald Stay E.S. Maroc. Nous attendons 
pour l'activité 2024 une nouvelle activité commerciale qui va être cette fois de Emerald Stay S.A. Suisse qui va s'occuper de faire de la location de chalet qui se trouve à Verbier. 
Donc dans le compte de résultats nous allons recevoir le chiffre d'affaires qui va venir de Muse. Muse qui est une plateforme appelée PMS pour Property Management System. 
C'est un système qui permet de faire de la gestion d'actifs tels que l'hôtellerie et ainsi de suite. Nous nous passons d'un point de vue comptable uniquement les 
transferts, les masses de flux provenant du revenu et les masses de flux provenant des mouvements bancaires car la quasi-totalité de ces paiements sont effectués au travers de Stripe.
Stripe pour qui je rappelle étant un fournisseur de paiements pour tout ce qui est 
par carte de crédit principalement. Donc à cela le business de Emerald Stay détient 
sous l'eau des propriétés de luxe qui sont principalement des résidences secondaires à des 
clients. Elle s'occupe de faire la promotion de ces derniers au travers de différentes plateformes telles que Airbnb, Booking et ainsi de suite. 
Et au fur et à mesure du temps la société a vu sa notoriété croître pour 
pouvoir bénéficier un maximum sur le volume d'affaires. 
Car elle touche principalement des commissions sur la marge intermédiaire entre le prix de 
vente de l'annuité qu'elle vend directement au consommateur final et au contrat actuel 
qu'elle s'engage auprès des personnes qui ont ces résidences de luxe. 
Car il faut savoir que ces résidences, les frais d'entretien pendant les périodes 
inhabitées peuvent coûter excessivement cher. 
Donc du coup cela permet, le business modèle d'Emerald Stay c'est de permettre la possibilité 
à ce que ces personnes puissent alléger leurs frais d'entretien ou bien même se faire de 
l'argent, rentabiliser les résidences de luxe qu'ils peuvent avoir au travers justement 
de ce revenu alternatif. Les grandes difficultés que nous avons avec Emerald Stay sont 
principalement d'un point de vue de taxes. Car beaucoup de ces propriétés sont en fait des 
propriétaires qui se trouvent à l'étranger ou pas forcément dans le lieu où se trouve le bien.
    Emerald Stay étant l'intermédiaire dedans devrait normalement selon les types de propriétés
qu'il a faire attention entre la part de retribution aux personnes physiques car cela peut impliquer différents retenus à la source. Vu aussi de la nouveauté de 
ce business modèle qui est basé sur la technologie, il est très fréquent que les 
réglementations fiscales et statutaires changent d'un pays à un autre. Donc ceci est un point 
assez sensible sur lequel il faut avoir les yeux sur le dossier d'Emerald Stay."""

# Exemple d'instruction
instruction = "Merci d'enlever le nom de Fany Chapelle et de remplacer et remplacer avec un terme générale afin de préserver l'anonymat."

# Utiliser l'agent pour mettre à jour le texte
#result = text_updater.force_update_text(instruction, original_text)

#print(result.get('updated_text', 'Erreur lors de la mise à jour du texte'))