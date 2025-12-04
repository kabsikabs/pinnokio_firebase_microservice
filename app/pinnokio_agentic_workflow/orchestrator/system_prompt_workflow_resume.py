"""
Prompt système pour la reprise de workflow après interaction utilisateur.

Ce prompt est utilisé quand :
1. Un workflow (tâche planifiée) était en cours
2. L'utilisateur est entré dans le chat et a interagi
3. L'utilisateur a envoyé "TERMINATE" ou a quitté le chat
4. Le workflow doit reprendre

Cas spéciaux :
- Si l'agent attend un callback LPT, il doit utiliser WAIT_ON_LPT
- Si des tâches restent, il continue l'exécution
- Si tout est terminé, il appelle TERMINATE_TASK

Text Wrapper :
- Le premier message de l'utilisateur en mode workflow est encapsulé
  avec des instructions expliquant la situation et les moyens de terminaison
"""

from typing import Optional, Dict, Any


def build_user_message_wrapper(
    user_message: str,
    is_first_message: bool = True,
    workflow_title: str = None,
    steps_summary: str = None
) -> str:
    """
    Encapsule le message de l'utilisateur avec un contexte de workflow.
    
    Appelé quand l'utilisateur envoie un message pendant un workflow actif.
    Explique la situation à l'agent et les moyens de terminer la conversation.
    
    Args:
        user_message: Le message original de l'utilisateur
        is_first_message: Si c'est le premier message de l'utilisateur depuis son entrée
        workflow_title: Titre de la tâche en cours (optionnel)
        steps_summary: Résumé des étapes (ex: "3/5 terminées")
        
    Returns:
        str: Message encapsulé avec contexte workflow
    """
    
    if not is_first_message:
        # Messages suivants : pas besoin de réexpliquer le contexte complet
        return f"""📩 **MESSAGE DE L'UTILISATEUR (workflow en pause)**

{user_message}

---
💡 Rappel : Pour reprendre le workflow, l'utilisateur peut :
- Terminer son message par `TERMINATE` pour reprendre le workflow
- Quitter le chat (le workflow reprendra automatiquement en arrière-plan)
"""
    
    # Premier message : contexte complet
    task_info = f"\n📋 **Tâche en cours :** {workflow_title}" if workflow_title else ""
    progress_info = f"\n📊 **Progression :** {steps_summary}" if steps_summary else ""
    
    return f"""
╔══════════════════════════════════════════════════════════════════════════╗
║  👤 L'UTILISATEUR EST ENTRÉ DANS LE CHAT - WORKFLOW EN PAUSE             ║
╚══════════════════════════════════════════════════════════════════════════╝
{task_info}{progress_info}

⚠️ **SITUATION ACTUELLE :**
Vous étiez en train d'exécuter un workflow planifié.
L'utilisateur vient d'entrer dans le chat et vous envoie un message.
Le workflow est maintenant EN PAUSE pour vous permettre de dialoguer avec lui.

---

📩 **MESSAGE DE L'UTILISATEUR :**

{user_message}

---

## 🎯 COMMENT RÉPONDRE

Vous êtes maintenant en **mode conversation normale** avec l'utilisateur.
Répondez à sa demande comme vous le feriez habituellement.

### Outils disponibles pendant la pause :
- ✅ Tous les outils SPT (GET_*, VIEW_*, etc.)
- ✅ `CRUD_STEP` pour modifier la checklist si l'utilisateur le demande
- ⚠️ Les LPT sont déconseillés pendant la conversation

### Si l'utilisateur veut modifier le plan :
- Utilisez `CRUD_STEP` avec `action: "create"` pour ajouter des étapes
- Utilisez `CRUD_STEP` avec `action: "update"` pour modifier une étape
- Utilisez `CRUD_STEP` avec `action: "delete"` pour supprimer une étape "pending"

---

## 🔄 TERMINAISON DE LA CONVERSATION

Le workflow reprendra dans l'un de ces cas :

1. **L'utilisateur termine son message par `TERMINATE`**
   → Vous recevrez son message (sans le mot TERMINATE)
   → Vous devez reprendre le workflow en mode UI (streaming activé)
   → L'utilisateur verra votre travail en temps réel

2. **L'utilisateur quitte le chat**
   → Le workflow reprendra automatiquement en mode BACKEND
   → Pas de streaming (l'utilisateur n'est plus connecté)
   → Vous travaillez de manière autonome

**IMPORTANT :** Ne tentez PAS de reprendre le workflow vous-même.
Attendez l'une des deux conditions ci-dessus.

---

Répondez maintenant au message de l'utilisateur. 👇
"""


def build_workflow_resume_prompt(
    user_context: dict,
    resume_reason: str,  # "terminate_request" | "user_left"
    user_message: Optional[str] = None,
    workflow_checklist: Optional[Dict[str, Any]] = None,
    active_lpt_tasks: Optional[list] = None,
    current_turn: int = 0
) -> str:
    """
    Construit le prompt système pour la reprise d'un workflow après interaction utilisateur.
    
    Args:
        user_context: Contexte utilisateur (company_name, etc.)
        resume_reason: Raison de la reprise ("terminate_request" ou "user_left")
        user_message: Message de l'utilisateur (si TERMINATE avec message)
        workflow_checklist: État actuel de la checklist workflow
        active_lpt_tasks: Liste des LPT en attente de callback
        current_turn: Numéro du tour actuel
        
    Returns:
        str: Prompt système pour la reprise
    """
    
    company_name = user_context.get("company_name", "la société")
    
    # Déterminer le contexte de reprise
    if resume_reason == "user_left":
        context_intro = """
🔄 **REPRISE DU WORKFLOW - L'UTILISATEUR A QUITTÉ LE CHAT**

L'utilisateur a quitté le chat pendant que vous étiez en conversation avec lui.
Vous devez maintenant reprendre le workflow là où vous l'avez laissé et continuer
l'exécution de manière autonome.
"""
    else:  # terminate_request
        context_intro = f"""
🔄 **REPRISE DU WORKFLOW - DEMANDE DE L'UTILISATEUR**

L'utilisateur vous a demandé de reprendre le workflow.
{f'Message de l utilisateur : "{user_message}"' if user_message else ''}

Tenez compte de son message si pertinent pour la suite du workflow.
"""
    
    # Section checklist
    checklist_section = ""
    pending_steps = []
    completed_steps = []
    in_progress_steps = []
    
    if workflow_checklist and workflow_checklist.get("steps"):
        steps = workflow_checklist.get("steps", [])
        
        for step in steps:
            step_id = step.get("id", "?")
            step_name = step.get("name", "Étape")
            step_status = step.get("status", "pending")
            step_message = step.get("message", "")
            
            if step_status == "completed":
                completed_steps.append(f"   ✅ `{step_id}` : {step_name} → {step_message}")
            elif step_status == "in_progress":
                in_progress_steps.append(f"   🔄 `{step_id}` : {step_name}")
            elif step_status == "error":
                pending_steps.append(f"   ❌ `{step_id}` : {step_name} (erreur: {step_message})")
            else:  # pending
                pending_steps.append(f"   ⏳ `{step_id}` : {step_name}")
        
        total = len(steps)
        done = len(completed_steps)
        
        checklist_section = f"""
📋 **ÉTAT DE VOTRE CHECKLIST** ({done}/{total} terminées)

**Terminées :**
{chr(10).join(completed_steps) if completed_steps else "   (aucune)"}

**En cours :**
{chr(10).join(in_progress_steps) if in_progress_steps else "   (aucune)"}

**À faire :**
{chr(10).join(pending_steps) if pending_steps else "   (aucune)"}
"""
    else:
        checklist_section = """
📋 **CHECKLIST**

Consultez votre historique de conversation pour retrouver les étapes de votre checklist.
"""
    
    # Section LPT en attente
    lpt_section = ""
    if active_lpt_tasks and len(active_lpt_tasks) > 0:
        lpt_list = "\n".join([f"   - {task}" for task in active_lpt_tasks])
        lpt_section = f"""
⏳ **TÂCHES LPT EN ATTENTE DE CALLBACK**

Les tâches suivantes ont été lancées et attendent un retour :
{lpt_list}

⚠️ **IMPORTANT** : Si la prochaine étape de votre workflow dépend du résultat
de ces LPT, vous devez appeler l'outil `WAIT_ON_LPT` pour attendre leur retour.
"""
    
    # Instructions de reprise
    instructions = """
---

## 📌 INSTRUCTIONS DE REPRISE

### ÉTAPE 1 : ÉVALUER LA SITUATION

1. Consultez votre checklist ci-dessus
2. Identifiez où vous en étiez
3. Vérifiez si des LPT sont en attente
4. Tenez compte du message de l'utilisateur (s'il y en a un)

### ÉTAPE 2 : DÉCIDER DE L'ACTION

**CAS A - Des étapes restent à faire et AUCUN LPT en attente :**
→ Marquez la prochaine étape en "in_progress" avec `CRUD_STEP` (action: update)
→ Exécutez les outils nécessaires (SPT ou LPT)
→ Continuez jusqu'à TERMINATE_TASK

**CAS B - Une étape en cours ATTEND un callback LPT :**
→ Appelez l'outil `WAIT_ON_LPT` avec les infos du LPT attendu
→ Le workflow se mettra en pause jusqu'au callback
→ Vous serez réactivé automatiquement quand le LPT terminera

**CAS C - Toutes les étapes sont terminées :**
→ Vérifiez que TOUTES les étapes ont le statut "completed"
→ Appelez `TERMINATE_TASK` avec un résumé complet

**CAS D - L'utilisateur a demandé une modification du plan :**
→ Utilisez `CRUD_STEP` pour ajuster la checklist
→ Puis continuez avec le nouveau plan

### ÉTAPE 3 : CONTINUER OU ATTENDRE

- Si vous pouvez continuer → Exécutez les outils nécessaires
- Si vous devez attendre un LPT → Utilisez `WAIT_ON_LPT`
- Si tout est fini → Utilisez `TERMINATE_TASK`

---

## 📝 OUTIL CRUD_STEP - Gestion de la Checklist

**Quand utiliser `CRUD_STEP` :**

1. **Ajouter une étape** (suite à une demande utilisateur ou un ajustement) :
```json
{
    "action": "create",
    "step_id": "STEP_4_VERIFICATION",
    "step_name": "Vérification des résultats",
    "insert_after": "STEP_3_TRAITEMENT"
}
```

2. **Mettre à jour le statut** (remplace UPDATE_STEP) :
```json
{
    "action": "update",
    "step_id": "STEP_2_SAISIE",
    "status": "completed",
    "message": "50 factures saisies avec succès"
}
```

3. **Supprimer une étape "pending"** (devenue inutile) :
```json
{
    "action": "delete",
    "step_id": "STEP_5_OPTIONNEL",
    "reason": "Non nécessaire suite au traitement automatique du LPT"
}
```

⚠️ **Règles importantes :**
- Vous ne pouvez supprimer QUE les étapes "pending"
- Les étapes "in_progress" ou "completed" NE peuvent PAS être supprimées

---

## 🛑 RÈGLE CRITIQUE : WAIT_ON_LPT

**Quand utiliser `WAIT_ON_LPT` :**

Utilisez cet outil si et SEULEMENT si :
1. Vous avez lancé un LPT (ex: LPT_APBookkeeper, LPT_Router, etc.)
2. Ce LPT n'a pas encore retourné son résultat (pas de callback reçu)
3. La suite de votre workflow DÉPEND du résultat de ce LPT

**Format d'appel :**
```json
{
    "reason": "Attente du retour de LPT_APBookkeeper pour la saisie des 5 factures",
    "expected_lpt": "LPT_APBookkeeper",
    "step_waiting": "STEP_2_SAISIE_FACTURES"
}
```

**CE QUI SE PASSE :**
- Le workflow se met en pause proprement
- Quand le LPT terminera, vous serez automatiquement réactivé
- Vous recevrez le résultat du LPT et pourrez continuer

---

## ⚠️ RÈGLE CRITIQUE : TERMINATE_TASK

**AVANT d'appeler TERMINATE_TASK, vérifiez que :**

1. ✅ TOUTES les étapes de votre checklist sont "completed"
2. ✅ Aucun LPT n'est en attente de callback
3. ✅ L'objectif de la mission est atteint

**Si des étapes ne sont pas "completed" :**
❌ L'appel à TERMINATE_TASK sera REFUSÉ
→ Vous devrez d'abord terminer ou mettre à jour les étapes restantes

---
"""
    
    # Assembler le prompt complet
    prompt = f"""# 🔄 MODE REPRISE WORKFLOW

## 🎯 CONTEXTE

Vous travaillez pour **{company_name}**.
Tour actuel : {current_turn}

{context_intro}

{checklist_section}

{lpt_section}

{instructions}

**Rappel** : Vous avez accès à tous les outils (SPT, LPT, WAIT_ON_LPT, UPDATE_STEP, TERMINATE_TASK).
Travaillez de manière autonome jusqu'à la fin du workflow.
"""
    
    return prompt


def build_workflow_resume_message(
    resume_reason: str,
    user_message: Optional[str] = None,
    has_pending_lpt: bool = False
) -> str:
    """
    Construit le message utilisateur à injecter pour la reprise du workflow.
    
    Args:
        resume_reason: "terminate_request" ou "user_left"
        user_message: Message de l'utilisateur (si TERMINATE)
        has_pending_lpt: Si des LPT sont en attente
        
    Returns:
        str: Message à injecter dans la conversation
    """
    
    if resume_reason == "user_left":
        base_message = """🔄 **REPRISE DU WORKFLOW**

L'utilisateur a quitté le chat. Reprenez le workflow là où vous l'avez laissé.

**Actions requises :**
1. Consultez votre checklist
2. Identifiez la prochaine étape à effectuer
3. Continuez l'exécution ou attendez un LPT si nécessaire
"""
    else:  # terminate_request
        user_part = f'\n\nMessage de l\'utilisateur : "{user_message}"' if user_message else ""
        base_message = f"""🔄 **REPRISE DU WORKFLOW**

L'utilisateur a demandé la reprise du workflow.{user_part}

**Actions requises :**
1. Consultez votre checklist
2. Tenez compte du message utilisateur si pertinent
3. Continuez l'exécution ou attendez un LPT si nécessaire
"""
    
    if has_pending_lpt:
        base_message += """
⚠️ **ATTENTION** : Des LPT sont en attente de callback. 
Si la suite dépend de leur résultat, utilisez `WAIT_ON_LPT`.
"""
    
    return base_message

