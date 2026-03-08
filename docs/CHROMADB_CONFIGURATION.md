# ChromaDB Configuration & Best Practices

## Current Configuration

### Server Setup (EC2 Instance)
- **Host**: `15.237.231.19`
- **Port**: `8000`
- **Region**: eu-west-3 (Paris)
- **Instance ID**: `i-0617efb08fecc6d2a`
- **Version**: ChromaDB 1.0.12

### Docker Configuration

```yaml
# docker-compose.yml (EC2)
version: '3.9'

networks:
  net:
    driver: bridge

services:
  server:
    image: ghcr.io/chroma-core/chroma:1.0.12
    volumes:
      - index_data:/data    # IMPORTANT: /data est le chemin par défaut de ChromaDB 1.0+
    ports:
      - 8000:8000
    networks:
      - net
    environment:
      - IS_PERSISTENT=TRUE
      - ANONYMIZED_TELEMETRY=FALSE

volumes:
  index_data:
    driver: local
```

### Data Persistence

**IMPORTANT**: ChromaDB 1.0+ utilise `/data/` comme répertoire par défaut, PAS `/chroma/chroma/`.

| Version | Chemin par défaut |
|---------|-------------------|
| 0.4.x | `/chroma/chroma/` |
| 1.0+ | `/data/` |

Pour vérifier que les données sont persistées correctement:

```bash
# Sur l'EC2
sudo ls -la /var/lib/docker/volumes/ec2-user_index_data/_data/
# Doit contenir: chroma.sqlite3

# Dans le container
docker exec <container_id> ls -la /data/
```

---

## ChromaDB 1.0+ New Features

### Version History
| Version | Date | Key Features |
|---------|------|--------------|
| 1.0.0 | April 3, 2025 | Major rewrite, API v2, new default paths |
| 1.1.0 | Sept 16, 2025 | Performance improvements |
| 1.2.0 | Oct 18, 2025 | Bug fixes |
| 1.3.0 | Oct 29, 2025 | New embedding functions (Nomic, Google GenAI, Transformers.js) |
| 1.4.0 | Dec 24, 2025 | **Group by** operator, CMEK support, Indexing status tracking |
| 1.4.1 | Jan 14, 2026 | Enhanced eventual consistency, Rust client improvements |

### Notable New Features

1. **Group By Operator** (1.4.0)
   - Permet de grouper les résultats de recherche par métadonnées

2. **Indexing Status Tracking** (1.4.0)
   - Suivi en temps réel de l'état d'indexation
   - Disponible dans Python, Rust, et TypeScript clients

3. **CMEK Support** (1.4.0)
   - Customer-Managed Encryption Keys
   - Pour les déploiements enterprise

4. **New Embedding Functions** (1.3.0+)
   - Nomic embeddings
   - Google GenAI embeddings
   - Transformers.js support

---

## Async Client

### Overview

ChromaDB 1.0+ offre un `AsyncHttpClient` pour les opérations non-bloquantes.

```python
import asyncio
import chromadb

async def main():
    # Client asynchrone
    client = await chromadb.AsyncHttpClient(
        host="15.237.231.19",
        port=8000,
        ssl=False
    )

    # Toutes les opérations sont async
    collection = await client.create_collection(name="my_collection")
    await collection.add(
        documents=["hello world"],
        ids=["id1"]
    )

    results = await collection.query(
        query_texts=["hello"],
        n_results=5
    )

asyncio.run(main())
```

### Async Methods Available

Toutes les méthodes synchrones ont leur équivalent async:

| Sync Method | Async Equivalent |
|-------------|------------------|
| `client.create_collection()` | `await client.create_collection()` |
| `collection.add()` | `await collection.add()` |
| `collection.query()` | `await collection.query()` |
| `collection.get()` | `await collection.get()` |
| `collection.delete()` | `await collection.delete()` |
| `collection.update()` | `await collection.update()` |
| `client.heartbeat()` | `await client.heartbeat()` |

### Known Issues

**Bug dans 1.0.0**: HTTP 422 "Unprocessable Entity" avec `AsyncHttpClient` et `get_or_create_collection`.
- **Status**: Corrigé dans versions ultérieures
- **Workaround**: Utiliser `create_collection` avec try/except

---

## Recommendation: Should We Use Async?

### Current Architecture Analysis

Notre `ChromaVectorService` actuel:
- Pattern Singleton thread-safe
- Client synchrone `chromadb.HttpClient`
- Utilisé principalement dans le workflow agentique (RAG_SEARCH tool)

### Avantages de l'Async

| Avantage | Impact pour notre système |
|----------|---------------------------|
| **Non-blocking I/O** | Permet de faire d'autres opérations pendant l'attente réseau |
| **Better concurrency** | Plusieurs requêtes ChromaDB simultanées |
| **Lower memory per request** | Meilleur pour les systèmes à haute charge |

### Inconvénients

| Inconvénient | Impact |
|--------------|--------|
| **Refactoring requis** | Toutes les méthodes doivent devenir `async` |
| **Propagation async** | Les appelants doivent aussi être async |
| **Debugging plus complexe** | Stack traces plus difficiles à lire |
| **Bug potentiels** | Version 1.0.0 avait des bugs async |

### Verdict: **OUI, mais avec précaution**

**Recommandation**: Migrer vers AsyncHttpClient pour les raisons suivantes:

1. **Notre système est déjà async** (`llm_manager.py` utilise `asyncio`)
2. **Multiple RAG searches** peuvent être parallélisées
3. **Meilleure scalabilité** pour ECS avec plusieurs workers

### Migration Complète (IMPLÉMENTÉE ✅)

La migration vers async a été implémentée dans `app/chroma_vector_service.py`:

```python
# ============================================================================
# Usage du service ASYNC (Recommandé pour workflows agentiques)
# ============================================================================

from app.chroma_vector_service import get_async_chroma_vector_service

# Initialisation
service = await get_async_chroma_vector_service()

# Recherche simple
results = await service.query_documents("collection_name", ["query text"], n_results=5)

# Recherches PARALLÈLES (optimisation majeure!)
results = await service.parallel_query(
    "collection_name",
    ["AVS cotisations", "LPP retraite", "bulletin salaire"],  # 3 requêtes
    n_results=5
)
# Toutes les recherches s'exécutent en parallèle → temps total = temps de la plus lente

# Ajout de documents
await service.add_documents(
    "collection_name",
    documents=["Document 1", "Document 2"],
    metadatas=[{"source": "test"}, {"source": "test"}]
)

# Suppression
await service.delete_documents("collection_name", ids=["id1", "id2"])

# ============================================================================
# Usage du service SYNC (Legacy - Rétrocompatibilité)
# ============================================================================

from app.chroma_vector_service import get_chroma_vector_service

# Même API mais synchrone (bloquant)
service = get_chroma_vector_service()
results = service.query_documents("collection_name", ["query text"])
```

### API Disponible

| Méthode | Sync | Async | Description |
|---------|------|-------|-------------|
| `query_documents()` | ✅ | ✅ | Recherche dans une collection |
| `parallel_query()` | ❌ | ✅ | **Recherches parallèles** |
| `add_documents()` | ✅ | ✅ | Ajouter des documents |
| `delete_documents()` | ✅ | ✅ | Supprimer des documents |
| `get_collection_info()` | ✅ | ✅ | Infos sur une collection |
| `delete_collection()` | ✅ | ✅ | Supprimer une collection |
| `heartbeat()` | ✅ | ✅ | Test de connexion |

### Helper pour contexte mixte

```python
from app.chroma_vector_service import run_async_query

# Depuis du code synchrone, exécuter une requête async
results = run_async_query("collection_name", ["query"], n_results=5)
```

### Notes d'implémentation

1. **Singleton pattern** - Une seule instance par type (sync/async)
2. **Cache des collections** - Évite les appels répétés à `get_or_create_collection`
3. **Thread-safe** - Utilise `asyncio.Lock()` pour le cache async
4. **Rétrocompatibilité** - L'API sync reste identique

---

## RAG_SEARCH Tool Integration

L'outil `RAG_SEARCH` est l'interface principale pour les agents Pinnokio vers ChromaDB.

### Fichiers

| Fichier | Description |
|---------|-------------|
| `app/pinnokio_agentic_workflow/tools/rag_search_tool.py` | Définition et handler de l'outil |
| `app/pinnokio_agentic_workflow/orchestrator/pinnokio_brain.py` | Méthodes `_async_spt_search_chromadb()` |

### Utilisation dans les agents

```python
from app.pinnokio_agentic_workflow.tools import create_rag_search_tool

# Dans _build_tools() d'un agent
tool_def, handler = create_rag_search_tool(self.brain)
tools.append(tool_def)
tool_mapping["RAG_SEARCH"] = handler
```

### Fonctionnalités

| Feature | Description |
|---------|-------------|
| **Async** | Utilise `AsyncChromaVectorService` |
| **Parallel queries** | Plusieurs recherches simultanées avec `parallel_queries` |
| **Filtres** | `department`, `doc_type`, `source` |
| **Search types** | `hierarchy`, `semantic`, `hybrid` |

### Méthodes Brain disponibles

```python
# Recherche simple async
result = await brain._async_spt_search_chromadb(
    query="contrat Jean Dupont",
    n_results=5,
    department="HR"
)

# Recherche parallèle (multiple requêtes simultanées)
result = await brain._async_parallel_search_chromadb(
    queries=["AVS cotisations", "LPP retraite", "bulletin salaire"],
    n_results=5
)
```

---

## SSH Access

Pour accéder à l'instance EC2:

```bash
# Récupérer la clé depuis Google Secret Manager
gcloud secrets versions access latest --secret="klk_router_aws_pem" > /tmp/key.pem
chmod 600 /tmp/key.pem

# Se connecter
ssh -i /tmp/key.pem ec2-user@15.237.231.19

# Commandes utiles
docker ps                                    # Liste containers
docker logs <container_id>                   # Logs ChromaDB
sudo du -sh /var/lib/docker/volumes/ec2-user_index_data/_data/  # Taille données
```

---

## Monitoring & Health Check

```bash
# Test de santé basique
curl http://15.237.231.19:8000/api/v2/heartbeat

# Liste des collections
curl http://15.237.231.19:8000/api/v2/tenants/default_tenant/databases/default_database/collections

# Créer une collection test
curl -X POST "http://15.237.231.19:8000/api/v2/tenants/default_tenant/databases/default_database/collections" \
  -H "Content-Type: application/json" \
  -d '{"name": "test_collection"}'
```

---

## References

- [ChromaDB Official Docs](https://docs.trychroma.com/)
- [ChromaDB Cookbook](https://cookbook.chromadb.dev/)
- [ChromaDB GitHub Releases](https://github.com/chroma-core/chroma/releases)
- [AsyncHttpClient Issue #4156](https://github.com/chroma-core/chroma/issues/4156)
