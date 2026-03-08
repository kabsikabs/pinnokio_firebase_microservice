# 🧪 Tests des Nouveaux Endpoints Cache

## 🚀 Démarrage rapide (30 secondes)

### Terminal 1 - Démarrer le serveur
```bash
start_server.bat
```

### Terminal 2 - Lancer les tests
```bash
run_tests.bat
```

C'est tout! 🎉

---

## 📋 Ce qui est testé

### FIREBASE_CACHE (5 endpoints)
✅ `get_mandate_snapshot` - Snapshot société
✅ `get_expenses` - Liste dépenses
✅ `get_ap_documents` - Documents APBookkeeper
✅ `get_bank_transactions` - Transactions bancaires
✅ `get_approval_pendinglist` - Liste approbations

### DRIVE_CACHE (3 endpoints)
✅ `get_documents` - Documents Google Drive
✅ `refresh_documents` - Force refresh
✅ `invalidate_cache` - Invalidation manuelle

---

## 📊 Résultats attendus

```
🧪 TEST: FIREBASE_CACHE Endpoints
==================================

📋 Test: FIREBASE_CACHE.get_mandate_snapshot
   Status: ✅
   Source: firebase
   Data exists: True

💰 Test: FIREBASE_CACHE.get_expenses
   Status: ✅
   Source: firebase
   Expenses count: 0

✅ Tests terminés!
```

**Important**:
- Premier appel → `source: firebase` (MISS)
- Deuxième appel → `source: cache` (HIT)
- C'est normal d'avoir 0 éléments si Firebase est vide

---

## 🔍 Vérifier le cache Redis

Si Redis CLI est installé:

```bash
# Se connecter
redis-cli

# Voir toutes les clés cache
KEYS cache:*

# Exemple de sortie:
1) "cache:test-user-123:test-company-456:expenses:details"
2) "cache:test-user-123:test-company-456:mandate:snapshot"
3) "cache:test-user-123:test-company-456:drive:documents"

# Voir le contenu d'une clé
GET cache:test-user-123:test-company-456:expenses:details

# Voir le TTL restant (en secondes)
TTL cache:test-user-123:test-company-456:expenses:details
```

---

## 🐛 Problèmes courants

### ❌ "Server not reachable"

Le serveur n'est pas démarré. Lancez:
```bash
start_server.bat
```

Attendez de voir:
```
INFO: Application startup complete.
```

### ❌ "No module named 'aiohttp'"

Installez aiohttp:
```bash
venv\Scripts\activate
pip install aiohttp
```

### ⚠️ "oauth_error": true (pour DRIVE_CACHE)

**C'est normal!** Les tests ne fournissent pas de credentials Google Drive valides.

Le backend gère proprement cette erreur et retourne:
```json
{
  "oauth_error": true,
  "error_message": "OAuth authentication required"
}
```

---

## 📈 Tester les performances

### Test 1: Cache MISS (lent)
```bash
# Première requête - va chercher dans Firebase
python test_cache_endpoints.py
# Noter le temps...
```

### Test 2: Cache HIT (rapide)
```bash
# Deuxième requête - va chercher dans Redis
python test_cache_endpoints.py
# Devrait être ~10x plus rapide
```

### Test 3: Invalider puis retester
```bash
# Invalider le cache
redis-cli FLUSHDB

# Retester - devrait être lent (MISS)
python test_cache_endpoints.py
```

---

## ✅ Validation complète

Pour valider que tout fonctionne:

1. ✅ Serveur démarre sans erreurs
2. ✅ Tous les tests passent (Status: ✅)
3. ✅ Premier appel: `source: firebase`
4. ✅ Deuxième appel: `source: cache`
5. ✅ Redis contient les clés: `redis-cli KEYS cache:*`
6. ✅ Logs backend montrent HIT/MISS

---

## 📚 Documentation complète

Pour plus de détails, consultez:

- **TEST_GUIDE.md** - Guide de test complet
- **MIGRATION.md** - Documentation technique (18 pages)
- **MIGRATION_SUMMARY.md** - Vue d'ensemble

---

## 🎯 Prochaines étapes

Après validation backend:

1. **Migrer les States frontend** (voir `MIGRATION_SUMMARY.md`)
2. **Tester le frontend** avec les nouveaux endpoints
3. **Nettoyer** l'ancien code cache frontend

Temps estimé: ~1h30 total

---

**Bon tests! 🚀**
