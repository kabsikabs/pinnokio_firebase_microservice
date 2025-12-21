# 📚 Index de la Documentation - Microservice Firebase
 
Ce fichier est un **point d’entrée stable**.
 
➡️ L’index principal a été déplacé ici :
 
- `doc/architecture/INDEX.md`
 
# 📚 Index de la Documentation - Microservice Firebase

## 🎯 Vue d'ensemble

Ce document sert de guide pour naviguer dans la documentation du microservice Firebase. La documentation est organisée par thématiques dans des sous-dossiers dédiés pour faciliter la recherche et la maintenance.

---

## 📁 Structure des dossiers

### 🏗️ **architecture/** - Architecture système

**Contenu :** Documentation technique sur l'architecture globale du système, les composants principaux, et les décisions de design.

**Fichiers :**
- `ARCHITECTURE_AGENTIQUE_COMPLETE.md` - Architecture complète du système agentique Pinnokio (structure multi-niveaux, agents, modes d'exécution)
- `ARCHITECTURE_INITIALIZE_SESSION_MULTI_USER.md` - Architecture d'initialisation des sessions multi-utilisateurs
- `ARCHITECTURE_OUTILS_AGENTS.md` - Architecture des outils disponibles pour les agents (SPT/LPT) + pattern d’ajout d’outils (incluant `GET_TASK_MANAGER_INDEX` / `GET_TASK_MANAGER_DETAILS`)
- `ARCHITECTURE_REDIS_JOBS_METRICS.md` - Architecture Redis pour les jobs et métriques
- `LLM_MICROSERVICE_ARCHITECTURE.md` - Architecture du microservice LLM (communication, isolation, scalabilité)
- `RESUME_INITIALIZE_SESSION_FR.md` - Résumé de l'initialisation des sessions (version française)

**Quand consulter :**
- Comprendre l'architecture globale du système
- Découvrir comment les composants interagissent
- Analyser les décisions de design
- Intégrer de nouveaux composants

---

### 🚀 **deployment/** - Déploiement et production

**Contenu :** Guides de déploiement, checklists, et procédures pour mettre en production le microservice.

**Fichiers :**
- `DEPLOYMENT_GUIDE.md` - Guide complet de déploiement (mode sécurisé, variables d'environnement, ECS)
- `DEPLOYMENT_CHECKLIST.md` - Checklist de vérification avant et après déploiement

**Quand consulter :**
- Préparer un déploiement en production
- Vérifier la configuration avant déploiement
- Résoudre des problèmes de déploiement
- Comprendre les variables d'environnement

---

### 🔌 **integration/** - Intégrations externes

**Contenu :** Documentation sur les intégrations avec des services externes (Reflex, LPT, WebSocket, etc.).

**Fichiers :**
- `DOCUMENTATION_VERIFICATION_SOLDE_LPT.md` - Documentation sur la vérification des soldes LPT
- `INTERMEDIATION_MODE_IMPLEMENTATION.md` - Implémentation du mode d'intermédiation
- `LPT_CALLBACK_SYSTEM.md` - Système de callbacks pour les Long Process Tools
- `LPT_PAYLOAD_FORMAT.md` - Format des payloads LPT
- `REFLEX_INTEGRATION.md` - Architecture de communication entre Reflex et le microservice
- `REFLEX_LLM_INTEGRATION.md` - Intégration LLM avec Reflex (version initiale)
- `REFLEX_LLM_INTEGRATION_FINAL.md` - Intégration LLM avec Reflex (version finale)
- `REFLEX_MODIFICATIONS_EXAMPLES.md` - Exemples de modifications pour Reflex
- `REFLEX_WEBSOCKET_STREAMING.md` - Streaming WebSocket avec Reflex
- `WEBSOCKET_FIXES.md` - Corrections et améliorations WebSocket

**Quand consulter :**
- Intégrer de nouveaux services externes
- Comprendre les protocoles de communication
- Déboguer des problèmes d'intégration
- Implémenter des callbacks ou webhooks

---

### 🔄 **workflow/** - Workflows et exécution de tâches

**Contenu :** Documentation sur les workflows, l'exécution de tâches, et la gestion des processus automatisés.

**Fichiers :**
- `FLUX_CONTEXTE_WORKFLOW_PARAMS.md` - Flux et contexte des paramètres de workflow
- `TASK_EXECUTION_MODE_API.md` - API pour le mode d'exécution de tâches
- `TASK_EXECUTION_WORKFLOW.md` - Workflow complet d'exécution des tâches (planifiées et à la demande)
- `TASK_EXECUTOR.md` - Documentation du Task Executor
- `WORKFLOW_CHECKLIST_USER_LANGUAGE.md` - Checklist pour les workflows avec gestion de la langue utilisateur
- `WORKFLOW_LISTENER_ON_DEMAND.md` - Listener pour workflows à la demande

**Quand consulter :**
- Créer ou modifier des workflows
- Comprendre l'exécution des tâches automatisées
- Déboguer des problèmes de workflow
- Implémenter de nouveaux types de tâches

---

### 🏢 **infrastructure/** - Infrastructure technique

**Contenu :** Documentation sur l'infrastructure technique (Redis, Chroma, bases de données, etc.).

**Fichiers :**
- `CHANGELOG_SCALABILITE_REDIS.md` - Changelog des améliorations de scalabilité Redis
- `Chroma_doc.md` - Documentation sur Chroma (base de données vectorielle)
- `infrastructure_docu.md` - Documentation générale de l'infrastructure
- `REDIS_ARCHITECTURE_COHERENTE_SCALABILITE.md` - Architecture Redis cohérente et scalable

**Quand consulter :**
- Comprendre l'infrastructure technique
- Optimiser les performances Redis
- Configurer des bases de données
- Planifier la scalabilité

---

### 📖 **guides/** - Guides pratiques

**Contenu :** Guides pratiques pour le développement, le debugging, et l'optimisation.

**Fichiers :**
- `DIAGNOSTIC_HEALTH_CHECK.md` - Guide de diagnostic et health check
- `GUIDE_LOGS_PERFORMANCE.md` - Guide sur les logs et la performance
- `LOG_ENRICHMENT_RECOMMENDATIONS.md` - Recommandations pour l'enrichissement des logs

**Quand consulter :**
- Déboguer des problèmes
- Optimiser les performances
- Améliorer la qualité des logs
- Effectuer des diagnostics système

---

### 👂 **listeners/** - Système de listeners

**Contenu :** Documentation sur le système de listeners et le registre centralisé.

**Fichiers :**
- `REGISTRY_LISTENERS.md` - Documentation du registre centralisé des listeners (détection zombies, debugging)

**Quand consulter :**
- Comprendre le système de listeners
- Déboguer des problèmes de listeners
- Détecter des listeners zombies
- Implémenter de nouveaux types de listeners

---

### 🎓 **onboarding/** - Onboarding et structure

**Contenu :** Documentation pour l'onboarding des développeurs et la structure des agents.

**Fichiers :**
- `Onboarding_agent_structure.md` - Structure d'onboarding des agents

**Quand consulter :**
- Onboarder de nouveaux développeurs
- Comprendre la structure des agents
- Créer de nouveaux agents

---

### 📦 **other/** - Autres documents

**Contenu :** Documents divers, résumés, et fichiers temporaires.

**Fichiers :**
- `instructions.md` - Instructions diverses
- `PHASE1_SUMMARY.md` - Résumé de la phase 1 du projet
- `recent_logs.json` - Logs récents (fichier temporaire)

**Quand consulter :**
- Documents de référence générale
- Résumés de phases de développement
- Fichiers temporaires de debug

---

## 🔍 Guide de recherche rapide

### Par besoin

| Besoin | Dossier | Fichiers clés |
|--------|---------|---------------|
| Comprendre l'architecture globale | `architecture/` | `ARCHITECTURE_AGENTIQUE_COMPLETE.md` |
| Déployer en production | `deployment/` | `DEPLOYMENT_GUIDE.md` |
| Intégrer avec Reflex | `integration/` | `REFLEX_INTEGRATION.md` |
| Créer un workflow | `workflow/` | `TASK_EXECUTION_WORKFLOW.md` |
| Optimiser Redis | `infrastructure/` | `REDIS_ARCHITECTURE_COHERENTE_SCALABILITE.md` |
| Déboguer un problème | `guides/` | `DIAGNOSTIC_HEALTH_CHECK.md` |
| Comprendre les listeners | `listeners/` | `REGISTRY_LISTENERS.md` |
| Onboarder un développeur | `onboarding/` | `Onboarding_agent_structure.md` |

### Par composant

| Composant | Dossiers pertinents |
|-----------|---------------------|
| **Agents** | `architecture/`, `onboarding/` |
| **LLM** | `architecture/LLM_MICROSERVICE_ARCHITECTURE.md`, `integration/REFLEX_LLM_*.md` |
| **Redis** | `infrastructure/REDIS_*.md`, `architecture/ARCHITECTURE_REDIS_*.md` |
| **Workflows** | `workflow/` |
| **Listeners** | `listeners/`, `workflow/WORKFLOW_LISTENER_*.md` |
| **LPT/SPT** | `integration/LPT_*.md`, `architecture/ARCHITECTURE_OUTILS_*.md` |
| **WebSocket** | `integration/REFLEX_WEBSOCKET_*.md`, `integration/WEBSOCKET_FIXES.md` |
| **Firebase RTDB** | `integration/REFLEX_*.md`, `architecture/LLM_MICROSERVICE_ARCHITECTURE.md` |

---

## 📝 Conventions de nommage

Les fichiers suivent une convention de nommage pour faciliter l'identification :

- **ARCHITECTURE_*** : Documents d'architecture technique
- **DEPLOYMENT_*** : Guides de déploiement
- **REFLEX_*** : Intégrations avec Reflex
- **LPT_*** : Documentation sur les Long Process Tools
- **TASK_EXECUTION_*** : Documentation sur l'exécution de tâches
- **WORKFLOW_*** : Documentation sur les workflows
- **REDIS_*** : Documentation sur Redis
- **GUIDE_*** : Guides pratiques
- **DIAGNOSTIC_*** : Documentation de diagnostic

---

## 🔄 Maintenance

Ce document doit être mis à jour lorsque :
- De nouveaux dossiers sont créés
- Des fichiers sont déplacés entre dossiers
- De nouvelles thématiques émergent
- La structure de la documentation change

---

## 📞 Contact

Pour toute question sur l'organisation de la documentation ou pour suggérer des améliorations, contactez l'équipe de développement.

---

*Dernière mise à jour : Décembre 2025*




