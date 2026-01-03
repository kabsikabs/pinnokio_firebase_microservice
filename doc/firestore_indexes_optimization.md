# Optimisation des Index Firestore pour `task_manager`

## 📊 Analyse des Requêtes

### Structure des requêtes `GET_TASK_MANAGER_INDEX`

**Filtres TOUJOURS présents :**
- `mandate_path == X` (obligatoire, sécurité)

**Filtres optionnels (égalité `==`) :**
- `department == X`
- `status_final == X`
- `status == X`
- `last_outcome == X`

**Filtres optionnels (comparaison) :**
- `started_at >= X` (started_from)
- `started_at <= X` (started_to)

**Tri TOUJOURS présent :**
- `order_by started_at DESC`

## 🎯 Index Optimal Recommandé

### Index Unique "Universel"

```
Collection: task_manager
Champs indexés (dans l'ordre) :
1. mandate_path (Ascending)      ← TOUJOURS présent
2. department (Ascending)        ← Optionnel, mais fréquent
3. status_final (Ascending)      ← Optionnel
4. status (Ascending)            ← Optionnel
5. last_outcome (Ascending)      ← Optionnel
6. started_at (Descending)       ← Pour tri + filtres de date
7. _name_ (Descending)           ← TOUJOURS en dernier (règle Firestore)
```

**⚠️ ORDRE CRITIQUE :** `_name_` doit TOUJOURS être le dernier champ dans un index composite Firestore.

### Pourquoi cet ordre ?

1. **Règles Firestore (OBLIGATOIRES) :**
   - Les filtres d'égalité (`==`) doivent venir AVANT les filtres de comparaison (`>=`, `<=`)
   - L'ordre des champs doit correspondre à l'ordre d'utilisation dans la requête
   - Le champ de tri (`order_by`) doit venir juste avant `_name_`
   - **`_name_` doit TOUJOURS être le dernier champ** (règle stricte de Firestore)

2. **Ordre logique :**
   - `mandate_path` en premier car TOUJOURS présent
   - Puis les filtres d'égalité optionnels par ordre de fréquence d'utilisation
   - `started_at` en dernier pour le tri et les filtres de date

## ✅ Avantages

1. **Un seul index** au lieu de 3 → moins de maintenance
2. **Couverture complète** de tous les cas d'usage
3. **Performance optimale** car l'ordre correspond aux requêtes
4. **Évolutif** : peut supporter de nouveaux filtres d'égalité

## ⚠️ Limitations Firestore

- **Maximum 6 champs** dans un index composite (hors `_name_`)
- Notre index utilise 5 champs + `started_at` + `_name_` = **7 champs total** ✅

## 🔄 Migration

1. **Créer le nouvel index** avec tous les champs
2. **Attendre** que l'index soit "Activé" (peut prendre quelques minutes)
3. **Tester** les requêtes pour vérifier qu'elles utilisent le nouvel index
4. **Supprimer** les anciens index (2 et 3) une fois confirmé

## 📝 Index à Supprimer (après migration)

- Index 2 : `department` (asc), `mandate_path` (asc), `status_final` (asc), `started_at` (desc), `_name_` (desc)
- Index 3 : `department` (asc), `mandate_path` (asc), `started_at` (desc), `_name_` (desc)

**Conserver :**
- Index 1 : `mandate_path` (asc), `started_at` (desc), `_name_` (desc)
  - Peut être utile pour les requêtes simples sans filtres supplémentaires
  - Ou supprimer aussi si le nouvel index couvre ce cas

## 🧪 Cas d'Usage Couverts

✅ `mandate_path` seul
✅ `mandate_path` + `department`
✅ `mandate_path` + `department` + `status_final`
✅ `mandate_path` + `department` + `status_final` + `status`
✅ `mandate_path` + `department` + `status_final` + `status` + `last_outcome`
✅ Tous les cas ci-dessus + filtres de date (`started_from`, `started_to`)
✅ Tous les cas ci-dessus + tri par `started_at DESC`

---

## 📦 Récupération de `department_data`

### Structure des données dans Firestore

Les documents `task_manager` contiennent un champ `department_data` qui est un dictionnaire avec des sous-clés par département :

```json
{
  "job_id": "job_123",
  "department": "banker",
  "mandate_path": "clients/.../mandates/...",
  "status": "completed",
  "status_final": "archived",
  "started_at": "2025-01-02T10:00:00Z",
  "department_data": {
    "banker": {
      "transaction_id": "txn_123",
      "journal_id": "bank_account_001",
      "amount": 5000.00,
      "partner_name": "Client ABC",
      // ... autres champs spécifiques au département banker
    },
    "APBookeeper": {
      // ... champs spécifiques APBookeeper (si présent)
    },
    "router": {
      // ... champs spécifiques router (si présent)
    }
  }
}
```

### ✅ Récupération actuelle

**Code dans `task_manager_tools.py` (ligne 277) :**
```python
"department_data": dd.get("department_data", {}),
```

**✅ Fonctionne correctement** car :
1. L'outil récupère le document complet avec `d.to_dict()`
2. `department_data` est inclus dans le document complet
3. Le dictionnaire entier est retourné dans les résultats

### ⚠️ Limitations de l'index

**L'index ne couvre PAS les champs imbriqués dans `department_data`** :
- ❌ Impossible de filtrer sur `department_data.banker.transaction_id`
- ❌ Impossible de filtrer sur `department_data.APBookeeper.invoice_id`
- ❌ Impossible de filtrer sur `department_data.router.drive_file_id`

**Pourquoi ?**
- Firestore ne peut pas créer d'index composite sur des champs imbriqués dans un dictionnaire
- Les champs doivent être au niveau racine du document pour être indexés

### ✅ Solution actuelle

**Pour récupérer les données de `department_data` :**
1. ✅ L'outil récupère bien `department_data` complet dans les résultats
2. ✅ Le filtrage se fait côté application après récupération (si nécessaire)
3. ✅ L'index couvre les champs de niveau racine (`department`, `status_final`, etc.)

**Exemple d'utilisation :**
```python
# Résultat de GET_TASK_MANAGER_INDEX
{
  "results": [
    {
      "job_id": "job_123",
      "department": "banker",
      "department_data": {
        "banker": {
          "transaction_id": "txn_123",
          "amount": 5000.00,
          // ... toutes les données spécifiques
        }
      }
    }
  ]
}
```

### 🔍 Conclusion

**✅ L'index et l'outil récupèrent correctement `department_data`** :
- Les données sont bien incluses dans les résultats
- Le filtrage sur les champs de niveau racine fonctionne (via l'index)
- Les données imbriquées dans `department_data` sont disponibles mais non indexables

**Si vous avez besoin de filtrer sur des champs dans `department_data` :**
- Option 1 : Filtrer côté application après récupération
- Option 2 : Aplatir la structure et créer des champs au niveau racine (ex: `banker_transaction_id`, `apbookeeper_invoice_id`)
