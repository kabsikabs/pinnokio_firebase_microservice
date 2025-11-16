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
                
                escaped_context = re.escape(context)
                # Vérifier si le contexte existe pour éviter les erreurs avec re.sub si le pattern n'est pas trouvé
                match_found = re.search(escaped_context, text)
                if not match_found:
                    return {"success": False, "error": f"Contexte '{context}' non trouvé dans le texte.", "updated_text": text}

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