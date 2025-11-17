# 🛡️ Documentation - Système de Vérification de Solde pour Outils LPT

## 📋 Vue d'ensemble

Ce système vérifie automatiquement le solde du compte utilisateur **AVANT** l'envoi de chaque outil LPT (Long Process Tooling) : APBookkeeper, Router, et Banker.

Si le solde est insuffisant, l'opération est **bloquée** et un message clair est retourné à l'agent pour inviter l'utilisateur à recharger son compte.

---

## 🏗️ Architecture de l'implémentation

### **1. Méthode centrale : `check_balance_before_lpt()`**

**Emplacement :** `app/pinnokio_agentic_workflow/tools/lpt_client.py` (lignes 55-147)

**Signature :**
```python
def check_balance_before_lpt(
    self, 
    user_id: str = None,
    mandate_path: str = None,
    estimated_cost: float = 1.0,
    lpt_tool_name: str = "LPT"
) -> Dict[str, Any]
```

**Fonctionnement :**

1. **Récupération du solde** via `FirebaseManagement.get_balance_info()`
   - Paramètres : `mandate_path` ou `user_id`
   - Retour : `current_balance`, `current_expenses`, `current_topping`

2. **Calcul du solde requis**
   ```python
   required_balance = estimated_cost * 1.2  # Marge de sécurité de 20%
   ```

3. **Comparaison**
   ```python
   is_sufficient = current_balance >= required_balance
   ```

4. **Retour du résultat**
   - Si **suffisant** : `{"sufficient": True, "current_balance": ..., "required_balance": ...}`
   - Si **insuffisant** : `{"sufficient": False, "message": "...", "missing_amount": ...}`

**Message type retourné à l'agent si insuffisant :**

```
⚠️ **SOLDE INSUFFISANT** ⚠️

L'exécution de l'outil **APBookkeeper** nécessite un solde minimum.

📊 **État du compte :**
• Solde actuel : **10.00 $**
• Solde requis : **12.00 $**
• Montant manquant : **2.00 $**

💡 **Action requise :**
Veuillez inviter l'utilisateur à **recharger son compte** depuis le tableau de bord
pour continuer à utiliser les services.

🔗 L'utilisateur peut recharger son compte dans la section **Facturation** du tableau de bord.
```

---

### **2. Intégration dans les méthodes `launch_*`**

#### **2.1 launch_apbookeeper (ligne 574)**

**Coût estimé :** `1.0$ par facture`

```python
async def launch_apbookeeper(self, ..., job_ids: List[str], ..., brain=None):
    # 1. Récupérer le contexte
    context = brain.get_user_context()
    mandate_path = context.get('mandate_path')
    
    # 2. Calculer le coût estimé
    estimated_cost = len(job_ids) * 1.0
    
    # 3. Vérifier le solde
    balance_check = self.check_balance_before_lpt(
        mandate_path=mandate_path,
        user_id=user_id,
        estimated_cost=estimated_cost,
        lpt_tool_name="APBookkeeper"
    )
    
    # 4. Bloquer si insuffisant
    if not balance_check.get("sufficient", False):
        return {
            "status": "insufficient_balance",
            "error": "Solde insuffisant pour exécuter cette opération",
            "balance_info": {...},
            "message": balance_check.get("message")
        }
    
    # 5. Continuer l'exécution normale...
```

#### **2.2 launch_router (ligne 1058)**

**Coût estimé :** `0.5$ par document`

```python
async def launch_router(self, ..., drive_file_id: str, ..., brain=None):
    # 1. Récupérer le contexte
    context = brain.get_user_context()
    mandate_path = context.get('mandate_path')
    
    # 2. Calculer le coût estimé
    estimated_cost = 0.5
    
    # 3. Vérifier le solde
    balance_check = self.check_balance_before_lpt(
        mandate_path=mandate_path,
        user_id=user_id,
        estimated_cost=estimated_cost,
        lpt_tool_name="Router"
    )
    
    # 4. Bloquer si insuffisant
    if not balance_check.get("sufficient", False):
        return {
            "status": "insufficient_balance",
            ...
        }
    
    # 5. Continuer l'exécution normale...
```

#### **2.3 launch_banker (ligne 1805)**

**Coût estimé :** `0.3$ par transaction`

```python
async def launch_banker(self, ..., transaction_ids: List[str], ..., brain=None):
    # 1. Récupérer le contexte
    context = brain.get_user_context()
    mandate_path = context.get('mandate_path')
    
    # 2. Calculer le coût estimé
    estimated_cost = len(transaction_ids) * 0.3
    
    # 3. Vérifier le solde
    balance_check = self.check_balance_before_lpt(
        mandate_path=mandate_path,
        user_id=user_id,
        estimated_cost=estimated_cost,
        lpt_tool_name="Banker"
    )
    
    # 4. Bloquer si insuffisant
    if not balance_check.get("sufficient", False):
        return {
            "status": "insufficient_balance",
            ...
        }
    
    # 5. Continuer l'exécution normale...
```

---

### **3. Intégration dans les méthodes `launch_*_all`**

Les versions `_all` fonctionnent de la même manière, mais calculent le coût total en fonction du nombre d'items à traiter :

#### **3.1 launch_apbookeeper_all (ligne 507)**

```python
# Compter le nombre de factures
apbookeeper_jobs = brain.jobs_data.get("APBOOKEEPER", {}).get("to_do", [])
nb_invoices = len(apbookeeper_jobs)

# Calculer le coût total
estimated_cost = nb_invoices * 1.0

# Vérifier le solde
balance_check = self.check_balance_before_lpt(...)
```

#### **3.2 launch_router_all (ligne 928)**

```python
# Compter le nombre de documents
router_jobs = brain.jobs_data.get("ROUTER", {}).get("to_process", [])
nb_documents = len(router_jobs)

# Calculer le coût total
estimated_cost = nb_documents * 0.5

# Vérifier le solde
balance_check = self.check_balance_before_lpt(...)
```

#### **3.3 launch_banker_all (ligne 1620)**

```python
# Compter le nombre de transactions
bank_data = brain.jobs_data.get("BANK", {})
unprocessed_transactions = bank_data.get("unprocessed", [])
nb_transactions = len(unprocessed_transactions)

# Calculer le coût total
estimated_cost = nb_transactions * 0.3

# Vérifier le solde
balance_check = self.check_balance_before_lpt(...)
```

---

## 🔄 Flux complet de vérification

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. AGENT APPELLE UN OUTIL LPT                                  │
│    (APBookkeeper, Router, Banker)                               │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. RÉCUPÉRATION DU CONTEXTE                                     │
│    context = brain.get_user_context()                           │
│    mandate_path = context.get('mandate_path')                   │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. CALCUL DU COÛT ESTIMÉ                                        │
│    - APBookkeeper: nb_factures * 1.0$                           │
│    - Router: nb_documents * 0.5$                                │
│    - Banker: nb_transactions * 0.3$                             │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. APPEL À check_balance_before_lpt()                           │
│    - Récupère le solde depuis Firebase                          │
│    - Calcule le solde requis (coût * 1.2)                       │
│    - Compare current_balance >= required_balance                │
└────────────────────┬────────────────────────────────────────────┘
                     │
            ┌────────┴────────┐
            │                 │
            ▼                 ▼
    ✅ SUFFISANT      ❌ INSUFFISANT
            │                 │
            ▼                 ▼
┌──────────────────┐  ┌──────────────────────────────────────────┐
│ Continuer        │  │ Retourner message d'erreur à l'agent    │
│ l'exécution      │  │                                          │
│ normale          │  │ {                                        │
│                  │  │   "status": "insufficient_balance",      │
│                  │  │   "message": "Veuillez recharger..."     │
│                  │  │ }                                        │
└──────────────────┘  └──────────────────────────────────────────┘
```

---

## 📊 Tableau récapitulatif des coûts

| **Outil LPT**              | **Coût unitaire** | **Coût par batch**           | **Marge de sécurité** |
|----------------------------|-------------------|------------------------------|-----------------------|
| APBookkeeper (1 facture)   | 1.0$              | `nb_factures * 1.0$`         | +20%                  |
| Router (1 document)        | 0.5$              | `nb_documents * 0.5$`        | +20%                  |
| Banker (1 transaction)     | 0.3$              | `nb_transactions * 0.3$`     | +20%                  |

**Exemple :**
- 3 factures APBookkeeper : `3 * 1.0$ = 3.0$` → Solde requis : `3.0$ * 1.2 = 3.6$`
- 2 documents Router : `2 * 0.5$ = 1.0$` → Solde requis : `1.0$ * 1.2 = 1.2$`
- 4 transactions Banker : `4 * 0.3$ = 1.2$` → Solde requis : `1.2$ * 1.2 = 1.44$`

---

## ⚙️ Configuration et personnalisation

### **1. Modifier les coûts estimés**

**Emplacement :** Dans chaque méthode `launch_*`

```python
# APBookkeeper : ligne ~604
estimated_cost = len(job_ids) * 1.0  # Changez 1.0 selon votre tarif

# Router : ligne ~1085
estimated_cost = 0.5  # Changez 0.5 selon votre tarif

# Banker : ligne ~1834
estimated_cost = len(transaction_ids) * 0.3  # Changez 0.3 selon votre tarif
```

### **2. Modifier la marge de sécurité**

**Emplacement :** `check_balance_before_lpt()` ligne ~91

```python
# Actuellement 20% de marge
required_balance = estimated_cost * 1.2

# Pour 30% de marge :
required_balance = estimated_cost * 1.3

# Pour 10% de marge :
required_balance = estimated_cost * 1.1
```

### **3. Désactiver la vérification (déconseillé)**

Si vous voulez désactiver temporairement la vérification :

```python
# Dans check_balance_before_lpt(), ligne ~131
return {
    "sufficient": True,  # Force toujours suffisant
    "current_balance": current_balance,
    "required_balance": required_balance,
    "estimated_cost": estimated_cost
}
```

---

## 🧪 Tests

### **Exécuter le script de test**

```bash
python test_balance_check_lpt.py
```

Ce script teste :
1. ✅ La méthode `check_balance_before_lpt()`
2. ✅ L'intégration dans `launch_apbookeeper` avec solde insuffisant
3. ✅ L'intégration dans `launch_router_all` avec solde insuffisant

### **Tests manuels recommandés**

1. **Tester avec un solde faible** (< 5$)
   - Essayer de lancer 3 factures APBookkeeper
   - Vérifier que l'opération est bloquée
   - Vérifier le message retourné à l'agent

2. **Tester avec un solde élevé** (> 50$)
   - Essayer de lancer les mêmes opérations
   - Vérifier que les opérations passent

3. **Tester les versions _all**
   - Vérifier que le coût total est bien calculé
   - Vérifier que le nombre d'items est affiché dans les logs

---

## 📝 Logs générés

### **Exemple de log avec solde suffisant**

```
[BALANCE_CHECK_APBookkeeper] 💰 Vérification solde - 
Solde actuel: 92.27$ | Requis: 3.60$ (coût estimé: 3.00$) | Statut: ✅ SUFFISANT

[LPT_APBookkeeper] ✅ Solde vérifié et suffisant (92.27$ >= 3.60$)
```

### **Exemple de log avec solde insuffisant**

```
[BALANCE_CHECK_APBookkeeper] 💰 Vérification solde - 
Solde actuel: 2.50$ | Requis: 3.60$ (coût estimé: 3.00$) | Statut: ❌ INSUFFISANT

[BALANCE_CHECK_APBookkeeper] ⚠️ SOLDE INSUFFISANT - Besoin de 1.10$ supplémentaires

[LPT_APBookkeeper] ❌ BLOCAGE - Solde insuffisant (2.50$ < 3.60$)
```

---

## 🐛 Dépannage

### **Problème : La vérification ne bloque pas les opérations**

**Solution :**
1. Vérifier que `brain` est bien passé en paramètre
2. Vérifier que `mandate_path` est présent dans le contexte
3. Vérifier les logs pour voir si la vérification est appelée

### **Problème : Erreur "Brain est requis"**

**Solution :**
Tous les outils LPT nécessitent maintenant le paramètre `brain`. Vérifiez que vous l'incluez dans l'appel :

```python
result = await lpt_client.launch_apbookeeper(
    user_id=user_id,
    company_id=company_id,
    thread_key=thread_key,
    job_ids=["abc", "def"],
    brain=brain  # ⭐ OBLIGATOIRE
)
```

### **Problème : Solde toujours à 0.0$**

**Solution :**
Vérifier que :
1. Le document `clients/{user_id}/billing/current_balance` existe dans Firestore
2. Les champs `current_balance`, `current_topping`, `current_expenses` sont présents
3. Le `mandate_path` ou `user_id` est correct

---

## ✅ Checklist de déploiement

- [x] Méthode `check_balance_before_lpt()` créée
- [x] Intégration dans `launch_apbookeeper`
- [x] Intégration dans `launch_router`
- [x] Intégration dans `launch_banker`
- [x] Intégration dans `launch_apbookeeper_all`
- [x] Intégration dans `launch_router_all`
- [x] Intégration dans `launch_banker_all`
- [x] Script de test créé
- [x] Documentation créée
- [ ] Tests en environnement de staging
- [ ] Tests en production avec solde réel
- [ ] Ajustement des coûts selon les tarifs réels
- [ ] Formation de l'équipe sur le nouveau système

---

## 🎯 Prochaines étapes recommandées

1. **Configuration dynamique des coûts**
   - Stocker les tarifs dans Firebase
   - Permettre l'ajustement sans redéploiement

2. **Historique des blocages**
   - Logger les tentatives bloquées dans Firestore
   - Créer un dashboard de suivi

3. **Notifications utilisateur**
   - Envoyer une notification email quand le solde est bas
   - Proposer un rechargement automatique

4. **Alertes proactives**
   - Alerter l'utilisateur avant que le solde soit insuffisant
   - Afficher un badge dans l'UI quand le solde est critique

---

## 📞 Support

Pour toute question ou problème :
- Consulter les logs dans `[BALANCE_CHECK_*]`
- Exécuter le script de test : `python test_balance_check_lpt.py`
- Vérifier la documentation du système de solde dans le frontend

---

**Version :** 1.0.0  
**Date :** 2025-11-17  
**Auteur :** Assistant IA  
**Fichier modifié :** `app/pinnokio_agentic_workflow/tools/lpt_client.py`

