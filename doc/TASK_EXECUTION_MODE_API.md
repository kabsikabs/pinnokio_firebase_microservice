# API Change: Mode d'exécution explicite dans les cartes de tâches

## 📋 Description

**Problème identifié :** Le mode d'exécution des tâches (`ON_DEMAND`, `SCHEDULED`, `ONE_TIME`, `NOW`) n'était pas encodé de manière explicite dans le JSON envoyé à REFLEX. Il fallait parser le texte de planification pour deviner le mode.

**Solution implémentée :** Ajout d'un champ `execution_mode` explicite dans le JSON des cartes de tâches.

## 🔄 Modifications apportées

### 1. Backend (Python) - `app/llm_service/llm_manager.py`

**Classe `ApprovalCardBuilder`** :
- Ajout du paramètre `execution_mode: str = None` dans `build_approval_card()`
- Ajout du champ `execution_mode` dans `cardsV2[0]` et `message.cardParams`
- Maintien de la compatibilité ascendante

**Fonction `request_approval_with_card`** :
- Extraction de `execution_mode` depuis `card_params`
- Passage explicite du mode à `build_approval_card()`

### 2. Tools (Python) - `app/pinnokio_agentic_workflow/tools/task_tools.py`

**Fonction `_prepare_and_request_approval`** :
- Ajout de `"execution_mode": execution_plan` dans `card_params`
- Passage du mode réel (`ON_DEMAND`, `SCHEDULED`, etc.) au lieu de texte formaté

## 📊 Format JSON modifié

### Avant (parsing du texte requis) :
```json
{
  "message": {
    "cardParams": {
      "title": "👆 Créer tâche manuelle",
      "text": "⏰ Planification : Exécution manuelle (pas de planification automatique)",
      "button_text": "✅ Créer la tâche manuelle"
    }
  }
}
```

### Après (champ explicite) :
```json
{
  "cardsV2": [{
    "cardId": "task_creation_approval",
    "execution_mode": "ON_DEMAND"
  }],
  "message": {
    "cardParams": {
      "title": "👆 Créer tâche manuelle",
      "text": "⏰ Planification : Exécution manuelle (pas de planification automatique)",
      "button_text": "✅ Créer la tâche manuelle",
      "execution_mode": "ON_DEMAND"
    }
  },
  "execution_mode": "ON_DEMAND"
}
```

## 🎯 Valeurs possibles du champ `execution_mode`

| Mode | Description | Titre carte | Bouton | Planification |
|------|-------------|-------------|---------|---------------|
| `ON_DEMAND` | Exécution manuelle après approbation | 👆 Créer tâche manuelle | ✅ Créer la tâche manuelle | Exécution manuelle (pas de planification automatique) |
| `SCHEDULED` | Exécution récurrente planifiée | 📅 Créer tâche SCHEDULED | ✅ Créer la tâche | Tous les jours à 09:00 (Europe/Zurich) |
| `ONE_TIME` | Exécution unique à date/heure précise | 📅 Créer tâche ONE_TIME | ✅ Créer la tâche | Une fois le 2024-12-25T14:30:00 (Europe/Zurich) |
| `NOW` | Exécution immédiate | 🚀 Exécuter immédiatement | ✅ Lancer l'exécution | Exécution immédiate (pas de planification) |

## 🔧 Migration côté REFLEX

### Code à modifier :

**Avant :**
```javascript
// ❌ Parsing du texte pour deviner le mode
const scheduleText = card.message.cardParams.text;
let executionMode;
if (scheduleText.includes('Exécution manuelle')) {
    executionMode = 'ON_DEMAND';
} else if (scheduleText.includes('Tous les jours')) {
    executionMode = 'SCHEDULED';
}
```

**Après :**
```javascript
// ✅ Lecture directe du champ explicite
const executionMode = card.execution_mode || card.message.cardParams.execution_mode;
```

### Fallback pour compatibilité :
```javascript
// ✅ Fallback si le champ n'existe pas encore
const executionMode = card.execution_mode ||
                     card.message.cardParams.execution_mode ||
                     parseExecutionModeFromText(card.message.cardParams.text);
```

## ✅ Avantages

1. **Fiabilité** : Plus d'erreur de parsing du texte
2. **Performance** : Lecture directe sans regex
3. **Maintenance** : Code plus clair et robuste
4. **Debugging** : Logs plus explicites
5. **Évolution** : Facilite l'ajout de nouveaux modes

## 🚨 Breaking Change : AUCUNE

- **100% backward compatible**
- Tous les champs existants préservés
- Champ `execution_mode` optionnel (null si non fourni)
- Parsing existant continue de fonctionner

## 📝 Tests recommandés

1. Créer une tâche `ON_DEMAND` → Vérifier `execution_mode: "ON_DEMAND"`
2. Créer une tâche `SCHEDULED` → Vérifier `execution_mode: "SCHEDULED"`
3. Créer une tâche `ONE_TIME` → Vérifier `execution_mode: "ONE_TIME"`
4. Créer une tâche `NOW` → Vérifier `execution_mode: "NOW"`
5. Vérifier que les anciennes cartes (sans le champ) fonctionnent encore

## 🎯 Priorité

**HAUTE** - Cette modification corrige un bug critique où le mode d'exécution n'était pas fiable côté REFLEX.

