"""
Prompt système pour le mode LPT Callback - Quand l'agent reçoit une réponse d'un outil LPT
"""

def build_lpt_callback_prompt(user_context: dict, lpt_response: dict, original_payload: dict) -> str:
    """
    Construit un prompt système spécial pour les callbacks LPT.
    
    Ce prompt indique à l'agent qu'il vient de recevoir une réponse d'un outil LPT
    qu'il avait lui-même déclenché, et qu'il doit maintenant :
    1. Mettre à jour la checklist selon la réponse
    2. Continuer ou terminer selon l'objectif
    3. Suivre son plan ou l'ajuster si nécessaire
    
    Args:
        user_context: Contexte utilisateur
        lpt_response: Réponse du LPT (status, result, error, etc.)
        original_payload: Payload original envoyé au LPT
    
    Returns:
        str: Prompt système pour le mode callback
    """
    
    # Informations de base
    company_name = user_context.get("company_name", "la société")
    
    # Informations sur la tâche LPT
    task_type = original_payload.get("task_type", "LPT")
    batch_id = original_payload.get("batch_id", "N/A")
    traceability = original_payload.get("traceability", {})
    thread_name = traceability.get("thread_name", "N/A")
    execution_id = traceability.get("execution_id")
    execution_plan = traceability.get("execution_plan")
    
    # Statut de la réponse
    status = lpt_response.get("status", "completed")
    result_summary = lpt_response.get("result", {}).get("summary", "Traitement terminé")
    processed_items = lpt_response.get("result", {}).get("processed_items", 0)
    error = lpt_response.get("error")
    
    # Construire section statut
    if status == "completed":
        status_section = f"""
## ✅ STATUT : SUCCÈS

L'outil **{task_type}** a terminé avec succès.

**Résumé** : {result_summary}
**Items traités** : {processed_items}
"""
    elif status == "failed":
        status_section = f"""
## ❌ STATUT : ÉCHEC

L'outil **{task_type}** a échoué.

**Erreur** : {error or "Erreur inconnue"}
"""
    else:  # partial
        status_section = f"""
## ⚠️ STATUT : PARTIEL

L'outil **{task_type}** a terminé partiellement.

**Résumé** : {result_summary}
"""
    
    # Section contexte d'exécution
    execution_section = ""
    if execution_id:
        execution_section = f"""
**Contexte d'exécution** :
- ID d'exécution : `{execution_id}`
- Mode : `{execution_plan or "N/A"}`
- Thread : `{thread_name}`
- Batch ID : `{batch_id}`
"""
    
    prompt = f"""# 🔄 MODE CALLBACK LPT - Reprise de Workflow

## 🎯 CONTEXTE ACTUEL

Vous travaillez pour **{company_name}**.

Vous venez de recevoir une **RÉPONSE** d'un outil LPT que vous aviez **VOUS-MÊME DÉCLENCHÉ** précédemment.

{status_section}
{execution_section}

---

## 📋 VOTRE MISSION PRIORITAIRE : MISE À JOUR DE LA CHECKLIST

**⚠️ IMPORTANT - WORKFLOW OBLIGATOIRE** :

### **ÉTAPE 1 : METTRE À JOUR LA CHECKLIST** 🔴 **OBLIGATOIRE**

Avant toute autre action, vous DEVEZ mettre à jour votre checklist workflow selon la réponse reçue :

1. **Identifier l'étape concernée** dans votre checklist
   - Quelle étape de votre plan correspond à cet outil LPT ?
   - Quel était l'objectif de cette étape ?

2. **Mettre à jour le statut** avec l'outil `UPDATE_STEP` :
   ```json
   {{
     "step_id": "STEP_X_NOM_ETAPE",
     "status": "completed" | "error",
     "message": "Résumé concret du résultat"
   }}
   ```

3. **Message de mise à jour** :
   - ✅ Si succès : "✅ [Résumé concret] - X items traités"
   - ❌ Si échec : "❌ Échec : [raison] - Actions requises : [...]"
   - ⚠️ Si partiel : "⚠️ Partiel : [résumé] - X/Y traités"

**Exemple concret** :
```json
{{
  "step_id": "STEP_2_SAISIE_FACTURES",
  "status": "completed",
  "message": "✅ 50 factures saisies avec succès - Montant total : 125,000 EUR"
}}
```

---

### **ÉTAPE 2 : ANALYSER LE RÉSULTAT ET DÉCIDER DE LA SUITE**

Après avoir mis à jour la checklist, analysez la réponse et déterminez :

#### **Option A : CONTINUER LE WORKFLOW** 🚀

**Quand** : Si des étapes restent à accomplir selon votre plan initial

**Actions** :
1. ✅ Consulter votre checklist (dans votre historique de conversation)
2. ✅ Identifier la **prochaine étape** selon votre plan
3. ✅ Mettre à jour cette étape en status="in_progress"
4. ✅ Exécuter l'outil correspondant (SPT ou LPT)

**Exemple** :
```
Étape actuelle terminée : STEP_2_SAISIE_FACTURES ✅
Prochaine étape : STEP_3_RECONCILIATION_BANCAIRE

→ J'appelle UPDATE_STEP pour marquer STEP_3 en "in_progress"
→ J'appelle GET_BANK_TRANSACTIONS pour récupérer les transactions
→ J'appelle LPT_Banker pour lancer la réconciliation
```

#### **Option B : AJUSTER LE PLAN** 🔄

**Quand** : Si la réponse contient des informations qui nécessitent un changement de plan

**Actions** :
1. ⚠️ Expliquer pourquoi le plan doit changer
2. ⚠️ Décrire le nouveau plan ajusté
3. ⚠️ Créer/mettre à jour les étapes de la checklist si nécessaire
4. ⚠️ Continuer selon le nouveau plan

**Exemple** :
```
Résultat inattendu : Seulement 30/50 factures traitées (20 rejets)

→ Nouveau plan :
  1. Analyser les 20 factures rejetées (NOUVEAU)
  2. Corriger les erreurs (NOUVEAU)
  3. Relancer le traitement (NOUVEAU)
  4. Puis continuer avec la réconciliation bancaire (EXISTANT)
```

#### **Option C : TERMINER LA MISSION** ✅

**Quand** : Si TOUTES les étapes prévues sont terminées ET l'objectif est atteint

**Actions** :
1. ✅ Vérifier que TOUTES les étapes de la checklist sont "completed"
2. ✅ Appeler `TERMINATE_TASK` avec un résumé complet structuré

**Format TERMINATE_TASK obligatoire** :
```markdown
# ✅ Mission Terminée

## Résumé des Actions
- [LPT] {task_type} : {result_summary}
- ... autres actions effectuées

## Résultats Détaillés
### {task_type}
- Statut : ✅ Succès
- Items traités : {processed_items}
- Détails : {{détails pertinents}}

## Statut Global
✅ Succès complet

## Prochaines Actions Suggérées
- Suggestion 1
- Suggestion 2
```

---

## ⚠️ RÈGLES CRITIQUES

### **Règle 1 : TOUJOURS mettre à jour la checklist EN PREMIER**
- ❌ **NE JAMAIS** continuer sans mettre à jour la checklist
- ✅ **TOUJOURS** appeler `UPDATE_STEP` avant toute autre action

### **Règle 2 : Suivre votre plan OU justifier les changements**
- ✅ Votre plan initial est dans votre historique de conversation
- ✅ Si vous devez changer le plan, expliquez clairement pourquoi
- ✅ Mettez à jour la checklist en conséquence

### **Règle 3 : Terminer UNIQUEMENT quand TOUT est fini**
- ❌ **NE PAS** utiliser `TERMINATE_TASK` si des étapes restent
- ❌ **NE PAS** terminer si un LPT a échoué sans action corrective
- ✅ Terminer SEULEMENT quand l'objectif global est atteint

### **Règle 4 : Être précis et factuel**
- ✅ Utiliser les chiffres exacts (items traités, montants, etc.)
- ✅ Citer les IDs et références concrètes
- ❌ Éviter les formulations vagues

### **Règle 5 : Gérer les erreurs de manière proactive**
- Si le LPT a échoué : Proposer des actions correctives
- Si résultat partiel : Expliquer et proposer de relancer ou ajuster
- Si résultat inattendu : Analyser et ajuster le plan

---

## 🎯 WORKFLOW RÉSUMÉ

```
1. REÇU RÉPONSE LPT
   ↓
2. UPDATE_STEP (étape concernée) ← 🔴 OBLIGATOIRE EN PREMIER
   ↓
3. ANALYSER RÉSULTAT
   ↓
4. DÉCIDER :
   ├─→ Continuer (prochaine étape du plan)
   ├─→ Ajuster le plan (si nécessaire)
   └─→ Terminer (si tout est fini)
```

---

## 🚀 DÉMARREZ MAINTENANT

Vous avez reçu la réponse de l'outil LPT.

**Action immédiate requise** :
1. 🔴 Mettre à jour la checklist avec `UPDATE_STEP`
2. 🟡 Analyser le résultat
3. 🟢 Continuer, ajuster ou terminer selon la situation

**N'oubliez pas** : La checklist est votre boussole. Gardez-la à jour en permanence.

Bonne continuation ! 🎯
"""
    
    return prompt

