# 🏗️ Schéma de Réorganisation de l'Architecture Agentique

**Date** : Décembre 2025  
**Version** : 1.0  
**Statut** : Proposition d'architecture

---

## 📊 Architecture Actuelle vs Architecture Proposée

### 🔴 Architecture Actuelle (État)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    LLMManager (Singleton)                            │
│                    └─→ LLMSession (par user:company)                  │
│                         └─→ PinnokioBrain (par thread)               │
│                              ├─→ BaseAIAgent (partagé)               │
│                              └─→ Outils selon chat_mode               │
└─────────────────────────────────────────────────────────────────────┘

Modes disponibles :
├─ general_chat      → Tous les outils (SPT + LPT + Core)
├─ onboarding_chat   → Tous les outils + écoute RTDB
├─ apbookeeper_chat  → Aucun outil (conversation uniquement)
├─ router_chat       → Aucun outil (conversation uniquement)
├─ banker_chat       → Aucun outil (conversation uniquement)
└─ task_execution    → Tous les outils + règles strictes
```

**Problèmes identifiés** :
- ❌ `general_chat` a trop d'outils (contexte lourd)
- ❌ Agents spécialisés (`apbookeeper_chat`, etc.) n'ont pas d'outils
- ❌ Pas de communication directe entre agents
- ❌ Logique métier dispersée dans `general_chat`

---

### 🟢 Architecture Proposée (Nouvelle)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    LLMManager (Singleton)                            │
│                    └─→ LLMSession (par user:company)                │
│                         ├─→ PinnokioBrain (general_chat)            │
│                         │   ├─→ BaseAIAgent (principal)             │
│                         │   └─→ Outils allégés :                     │
│                         │       ├─ SPT Tools (GET_FIREBASE, etc.)   │
│                         │       ├─ Core Tools (TERMINATE, etc.)      │
│                         │       └─ Agent Tools (NOUVEAU) :          │
│                         │           ├─ LAUNCH_APBOOKEEPER_AGENT    │
│                         │           ├─ LAUNCH_ROUTER_AGENT          │
│                         │           ├─ LAUNCH_BANKER_AGENT         │
│                         │           └─ ASK_AGENT (communication)    │
│                         │                                           │
│                         ├─→ ApBookeeperAgent (autonome)             │
│                         │   ├─→ BaseAIAgent (propre)               │
│                         │   ├─→ Prompt spécialisé                    │
│                         │   ├─→ Outils métier :                     │
│                         │   │   ├─ PROCESS_INVOICE                  │
│                         │   │   ├─ GET_INVOICE_STATUS               │
│                         │   │   └─ ASK_GENERAL_AGENT (communication) │
│                         │   └─→ Écoute RTDB (logs)                   │
│                         │                                           │
│                         ├─→ RouterAgent (autonome)                  │
│                         │   ├─→ BaseAIAgent (propre)                │
│                         │   ├─→ Prompt spécialisé                   │
│                         │   ├─→ Outils métier :                     │
│                         │   │   ├─ ROUTE_DOCUMENT                   │
│                         │   │   ├─ GET_ROUTING_STATUS              │
│                         │   │   └─ ASK_GENERAL_AGENT (communication) │
│                         │   └─→ Écoute RTDB (logs)                  │
│                         │                                           │
│                         └─→ BankerAgent (autonome)                  │
│                             ├─→ BaseAIAgent (propre)                │
│                             ├─→ Prompt spécialisé                   │
│                             ├─→ Outils métier :                     │
│                             │   ├─ RECONCILE_TRANSACTION            │
│                             │   ├─ GET_RECONCILIATION_STATUS        │
│                             │   └─ ASK_GENERAL_AGENT (communication) │
│                             └─→ Écoute RTDB (logs)                  │
└─────────────────────────────────────────────────────────────────────┘
```

**Avantages** :
- ✅ `general_chat` allégé (moins d'outils, contexte réduit)
- ✅ Agents spécialisés autonomes avec leurs propres outils
- ✅ Communication bidirectionnelle entre agents
- ✅ Logique métier centralisée dans chaque agent spécialisé
- ✅ Scalabilité : chaque agent peut évoluer indépendamment

---

## 🔄 Flux de Communication

### 1. Lancement d'un Agent Spécialisé

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Utilisateur : "Traite les factures du dossier X"            │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. general_chat analyse la requête                              │
│    → Détecte : besoin d'ApBookeeper                             │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. general_chat appelle LAUNCH_APBOOKEEPER_AGENT               │
│    {                                                             │
│      "job_ids": ["file_123", "file_456"],                       │
│      "instructions": "Vérifier les montants HT/TTC",           │
│      "thread_key": "job_abc123"  // Thread dédié                │
│    }                                                             │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Système crée ApBookeeperAgent                                │
│    ├─→ BaseAIAgent (propre, isolé)                              │
│    ├─→ Prompt spécialisé chargé                                 │
│    ├─→ Outils métier activés                                     │
│    └─→ Thread RTDB : active_chats/job_abc123                    │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. ApBookeeperAgent démarre le traitement                       │
│    ├─→ Appelle PROCESS_INVOICE pour chaque facture              │
│    ├─→ Écoute RTDB pour logs en temps réel                      │
│    └─→ Met à jour le statut dans Firebase                       │
└─────────────────────────────────────────────────────────────────┘
```

### 2. Communication Agent Spécialisé → General Chat

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. ApBookeeperAgent rencontre un problème                        │
│    Ex: "Facture avec TVA ambiguë, besoin de clarification"     │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. ApBookeeperAgent appelle ASK_GENERAL_AGENT                   │
│    {                                                             │
│      "question": "Comment gérer TVA mixte sur facture X ?",     │
│      "context": {                                                │
│        "job_id": "file_123",                                     │
│        "invoice_data": {...}                                    │
│      }                                                           │
│    }                                                             │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. general_chat reçoit la question                              │
│    ├─→ Analyse avec son contexte global                          │
│    ├─→ Peut utiliser ses outils (GET_FIREBASE, etc.)            │
│    └─→ Génère une réponse                                        │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Réponse retournée à ApBookeeperAgent                          │
│    {                                                             │
│      "answer": "Pour TVA mixte, créer lignes séparées...",      │
│      "suggestions": [...]                                        │
│    }                                                             │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. ApBookeeperAgent continue avec la réponse                     │
│    └─→ Applique la solution suggérée                            │
└─────────────────────────────────────────────────────────────────┘
```

### 3. Communication General Chat → Agent Spécialisé

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Utilisateur : "Où en est la facture file_123 ?"             │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. general_chat détecte : question sur job spécifique           │
│    → Appelle ASK_APBOOKEEPER_AGENT                               │
│    {                                                             │
│      "question": "Statut de la facture file_123",               │
│      "job_id": "file_123"                                       │
│    }                                                             │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. ApBookeeperAgent reçoit la question                           │
│    ├─→ Charge le contexte du job                                 │
│    ├─→ Utilise GET_INVOICE_STATUS                               │
│    └─→ Génère une réponse détaillée                              │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Réponse retournée à general_chat                              │
│    {                                                             │
│      "answer": "Facture en cours de traitement, étape 3/5...",  │
│      "status": "in_progress",                                    │
│      "details": {...}                                            │
│    }                                                             │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. general_chat présente la réponse à l'utilisateur              │
│    └─→ Formatage adapté au contexte général                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Outils Proposés

### Outils pour `general_chat` (Allégés)

**Outils conservés** :
- ✅ SPT Tools : `GET_FIREBASE_DATA`, `SEARCH_CHROMADB`, `GET_USER_CONTEXT`
- ✅ ContextTools : `ROUTER_PROMPT`, `APBOOKEEPER_CONTEXT`, `BANK_CONTEXT`, `COMPANY_CONTEXT`, `UPDATE_CONTEXT`
- ✅ Core Tools : `TERMINATE_TASK`, `VIEW_DRIVE_DOCUMENT`, `GET_TOOL_HELP`
- ✅ Task Tools : `CREATE_TASK`, `CREATE_CHECKLIST`, `UPDATE_STEP`, `CRUD_STEP`, `WAIT_ON_LPT`

**Outils supprimés** (déplacés vers agents spécialisés) :
- ❌ `GET_APBOOKEEPER_JOBS` → Déplacé vers ApBookeeperAgent
- ❌ `GET_ROUTER_JOBS` → Déplacé vers RouterAgent
- ❌ `GET_BANK_TRANSACTIONS` → Déplacé vers BankerAgent
- ❌ `LPT_APBookeeper` → Remplacé par `LAUNCH_APBOOKEEPER_AGENT`
- ❌ `LPT_Router` → Remplacé par `LAUNCH_ROUTER_AGENT`
- ❌ `LPT_Banker` → Remplacé par `LAUNCH_BANKER_AGENT`

**Nouveaux outils** :
- ⭐ `LAUNCH_APBOOKEEPER_AGENT` : Lance un agent ApBookeeper pour traiter des factures
- ⭐ `LAUNCH_ROUTER_AGENT` : Lance un agent Router pour router des documents
- ⭐ `LAUNCH_BANKER_AGENT` : Lance un agent Banker pour rapprocher des transactions
- ⭐ `ASK_APBOOKEEPER_AGENT` : Pose une question à l'agent ApBookeeper
- ⭐ `ASK_ROUTER_AGENT` : Pose une question à l'agent Router
- ⭐ `ASK_BANKER_AGENT` : Pose une question à l'agent Banker

---

### Outils pour `ApBookeeperAgent`

**Outils métier** :
- ⭐ `PROCESS_INVOICE` : Traite une facture (remplace LPT_APBookeeper)
- ⭐ `GET_INVOICE_STATUS` : Récupère le statut d'une facture
- ⭐ `GET_INVOICES` : Liste les factures à traiter (remplace GET_APBOOKEEPER_JOBS)
- ⭐ `UPDATE_INVOICE_INSTRUCTIONS` : Met à jour les instructions pour une facture
- ⭐ `ASK_GENERAL_AGENT` : Pose une question à l'agent général

**Outils système** :
- ✅ `GET_FIREBASE_DATA` : Accès Firestore (lecture)
- ✅ `GET_USER_CONTEXT` : Contexte utilisateur

---

### Outils pour `RouterAgent`

**Outils métier** :
- ⭐ `ROUTE_DOCUMENT` : Route un document vers un département (remplace LPT_Router)
- ⭐ `GET_ROUTING_STATUS` : Récupère le statut du routage
- ⭐ `GET_DOCUMENTS_TO_ROUTE` : Liste les documents à router (remplace GET_ROUTER_JOBS)
- ⭐ `UPDATE_ROUTING_INSTRUCTIONS` : Met à jour les instructions de routage
- ⭐ `ASK_GENERAL_AGENT` : Pose une question à l'agent général

**Outils système** :
- ✅ `GET_FIREBASE_DATA` : Accès Firestore (lecture)
- ✅ `GET_USER_CONTEXT` : Contexte utilisateur

---

### Outils pour `BankerAgent`

**Outils métier** :
- ⭐ `RECONCILE_TRANSACTION` : Rapproche une transaction bancaire (remplace LPT_Banker)
- ⭐ `GET_RECONCILIATION_STATUS` : Récupère le statut du rapprochement
- ⭐ `GET_TRANSACTIONS` : Liste les transactions à rapprocher (remplace GET_BANK_TRANSACTIONS)
- ⭐ `UPDATE_RECONCILIATION_INSTRUCTIONS` : Met à jour les instructions de rapprochement
- ⭐ `ASK_GENERAL_AGENT` : Pose une question à l'agent général

**Outils système** :
- ✅ `GET_FIREBASE_DATA` : Accès Firestore (lecture)
- ✅ `GET_USER_CONTEXT` : Contexte utilisateur

---

## 📋 Structure des Données

### Thread Management

**Threads `general_chat`** :
- Format : `chat_{uuid}` ou `task_{uuid}`
- Container : `chats/{thread_key}/messages`
- Agent : `PinnokioBrain` (general_chat)

**Threads agents spécialisés** :
- Format : `apbookeeper_{job_id}`, `router_{job_id}`, `banker_{job_id}`
- Container : `active_chats/{thread_key}/messages`
- Agent : `ApBookeeperAgent`, `RouterAgent`, `BankerAgent`

**Liaison** :
- Chaque thread agent spécialisé peut référencer le thread `general_chat` parent
- Le thread `general_chat` peut référencer les threads agents spécialisés créés

---

## 🔄 Cycle de Vie des Agents Spécialisés

### Création

```
1. general_chat appelle LAUNCH_APBOOKEEPER_AGENT
   └─→ Système crée :
       ├─ ApBookeeperAgent (instance)
       ├─ BaseAIAgent (propre, isolé)
       ├─ Thread RTDB : active_chats/apbookeeper_{job_id}
       └─ Écoute RTDB activée
```

### Exécution

```
2. ApBookeeperAgent traite les jobs
   ├─→ Appelle PROCESS_INVOICE pour chaque facture
   ├─→ Écoute RTDB pour logs en temps réel
   ├─→ Met à jour Firebase (statut, résultats)
   └─→ Peut appeler ASK_GENERAL_AGENT si besoin
```

### Communication

```
3. Communication bidirectionnelle
   ├─→ ApBookeeperAgent → general_chat : ASK_GENERAL_AGENT
   └─→ general_chat → ApBookeeperAgent : ASK_APBOOKEEPER_AGENT
```

### Fin de Vie

```
4. Agent se termine
   ├─→ Tous les jobs traités
   ├─→ Statut final sauvegardé dans Firebase
   ├─→ Thread RTDB archivé (optionnel)
   └─→ Instance agent supprimée (garbage collection)
```

---

## 🎯 Avantages de la Nouvelle Architecture

### 1. Allègement du Contexte `general_chat`

**Avant** :
- ~9500 tokens pour définitions d'outils
- ~40000 tokens pour system prompt
- **Total** : ~50000 tokens

**Après** :
- ~3500 tokens pour définitions d'outils (allégées)
- ~20000 tokens pour system prompt (allégé)
- **Total** : ~23500 tokens (**-53%**)

### 2. Séparation des Responsabilités

- ✅ `general_chat` : Orchestration et coordination
- ✅ `ApBookeeperAgent` : Logique métier factures
- ✅ `RouterAgent` : Logique métier routage
- ✅ `BankerAgent` : Logique métier rapprochement

### 3. Scalabilité

- ✅ Chaque agent peut évoluer indépendamment
- ✅ Ajout de nouveaux agents sans impacter `general_chat`
- ✅ Tests unitaires par agent

### 4. Communication Flexible

- ✅ Agents peuvent demander de l'aide à `general_chat`
- ✅ `general_chat` peut interroger les agents spécialisés
- ✅ Communication asynchrone via RTDB

---

## ❓ Questions à Clarifier

### 1. Architecture Technique

**Q1.1** : Les agents spécialisés doivent-ils avoir leur propre `BaseAIAgent` isolé, ou peuvent-ils partager celui de la session ?

**Q1.2** : Les agents spécialisés doivent-ils être persistants (survivent après traitement) ou éphémères (supprimés après traitement) ?

**Q1.3** : Comment gérer les agents spécialisés en mode BACKEND (tâches planifiées) ? Doivent-ils être créés automatiquement ?

### 2. Communication

**Q2.1** : La communication entre agents doit-elle être synchrone (attente réponse) ou asynchrone (callback) ?

**Q2.2** : Les questions `ASK_GENERAL_AGENT` doivent-elles être limitées en nombre ou en fréquence ?

**Q2.3** : Comment gérer les questions en cascade (agent A → general_chat → agent B) ?

### 3. Gestion des Jobs

**Q3.1** : Les jobs doivent-ils être envoyés directement aux agents spécialisés, ou via `general_chat` ?

**Q3.2** : Comment gérer les jobs multiples (batch) ? Un agent par job ou un agent pour tous les jobs ?

**Q3.3** : Les agents spécialisés doivent-ils pouvoir créer de nouveaux jobs (ex: ApBookeeper crée un job Router) ?

### 4. Écoute RTDB

**Q4.1** : Les agents spécialisés doivent-ils continuer à écouter RTDB pour les logs, ou utiliser un autre mécanisme ?

**Q4.2** : Comment synchroniser les logs RTDB avec les réponses des agents spécialisés ?

### 5. Migration

**Q5.1** : Comment migrer les threads existants (`apbookeeper_chat`, etc.) vers la nouvelle architecture ?

**Q5.2** : Faut-il maintenir une compatibilité ascendante avec les anciens modes ?

**Q5.3** : Comment gérer les workflows en cours pendant la migration ?

---

## 📝 Prochaines Étapes

1. **Répondre aux questions** ci-dessus
2. **Valider le schéma** d'architecture
3. **Définir les interfaces** des outils de communication
4. **Créer le plan d'implémentation** détaillé
5. **Implémenter par phases** (migration progressive)

---

**Version** : 1.0  
**Date** : Décembre 2025  
**Auteur** : Proposition d'architecture
