# 🧪 Guide de test de la migration cache

## 🚀 Démarrage rapide

### 1. Démarrer le serveur backend

```bash
cd C:\Users\Cedri\Coding\firebase_microservice
venv\Scripts\activate
python -m uvicorn app.main:app --reload --port 8000
```

Attendez de voir:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### 2. Dans un autre terminal, lancer les tests

```bash
cd C:\Users\Cedri\Coding\firebase_microservice
venv\Scripts\activate
python test_cache_endpoints.py
```

---

## 🔍 Tests manuels avec curl (Windows PowerShell)

### Test FIREBASE_CACHE.get_expenses

```powershell
$body = @{
    method = "FIREBASE_CACHE.get_expenses"
    kwargs = @{
        company_id = "test-company"
    }
    user_id = "test-user"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/rpc" -Method Post -Body $body -ContentType "application/json"
```

### Test FIREBASE_CACHE.get_mandate_snapshot

```powershell
$body = @{
    method = "FIREBASE_CACHE.get_mandate_snapshot"
    kwargs = @{
        company_id = "test-company"
    }
    user_id = "test-user"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/rpc" -Method Post -Body $body -ContentType "application/json"
```

### Test DRIVE_CACHE.get_documents

```powershell
$body = @{
    method = "DRIVE_CACHE.get_documents"
    kwargs = @{
        company_id = "test-company"
        input_drive_id = "test-drive-id"
    }
    user_id = "test-user"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/rpc" -Method Post -Body $body -ContentType "application/json"
```

---

## 📋 Ce qu'il faut vérifier

### ✅ Réponses attendues

Chaque appel RPC devrait retourner:

```json
{
  "ok": true,
  "data": {
    "data": [...],          // Les données elles-mêmes
    "source": "cache" ou "firebase",
    "oauth_error": false    // Seulement pour DRIVE_CACHE
  }
}
```

### 🔍 Logs backend à surveiller

**Premier appel (MISS)** - Données depuis source:
```
❌ [FIREBASE_CACHE] MISS: cache:test-user:test-company:expenses:details
✅ [FIREBASE_CACHE] Stockage réussi: cache:test-user:test-company:expenses:details | TTL: 2400s
```

**Deuxième appel (HIT)** - Données depuis cache:
```
✅ [FIREBASE_CACHE] HIT: cache:test-user:test-company:expenses:details | Cached: 2026-01-16T10:00:00 | Items: 42
```

### 📊 Vérifier Redis directement

Si Redis est installé localement:

```bash
# Se connecter à Redis
redis-cli

# Voir toutes les clés cache
KEYS cache:*

# Voir une clé spécifique
GET cache:test-user:test-company:expenses:details

# Voir le TTL restant
TTL cache:test-user:test-company:expenses:details
```

---

## 🐛 Problèmes courants

### ❌ "Connection refused"

**Cause**: Le serveur n'est pas démarré

**Solution**:
```bash
cd firebase_microservice
venv\Scripts\activate
python -m uvicorn app.main:app --reload --port 8000
```

### ❌ "KeyError: 'FIREBASE_CACHE.get_expenses'"

**Cause**: Les nouveaux handlers ne sont pas chargés

**Solution**: Redémarrer le serveur (Ctrl+C puis relancer)

### ❌ "No module named 'aiohttp'"

**Cause**: aiohttp n'est pas installé

**Solution**:
```bash
pip install aiohttp
```

### ⚠️ "oauth_error": true

**Cause**: Credentials Google Drive manquants (NORMAL pour les tests)

**Comportement**: Le backend retourne cette erreur proprement, c'est attendu si vous n'avez pas de credentials valides.

---

## 📈 Tests de performance

### Test de cache HIT vs MISS

1. **Première requête** (cache MISS):
   ```bash
   # Mesurer le temps
   python test_cache_endpoints.py
   # Noter le temps de réponse
   ```

2. **Deuxième requête** (cache HIT):
   ```bash
   # Relancer immédiatement
   python test_cache_endpoints.py
   # Le temps devrait être ~10x plus rapide
   ```

3. **Invalider et retester**:
   ```powershell
   # Invalider le cache
   $body = @{
       method = "FIREBASE_CACHE.invalidate_cache"
       kwargs = @{
           user_id = "test-user"
           company_id = "test-company"
           data_type = "expenses"
           sub_type = "details"
       }
       user_id = "test-user"
   } | ConvertTo-Json

   Invoke-RestMethod -Uri "http://localhost:8000/rpc" -Method Post -Body $body -ContentType "application/json"

   # Puis retester - devrait être lent (MISS)
   python test_cache_endpoints.py
   ```

---

## 🎯 Tests frontend (après migration)

Une fois qu'un State est migré (ex: expense_state.py):

### 1. Démarrer le backend
```bash
cd firebase_microservice
python -m uvicorn app.main:app --reload --port 8000
```

### 2. Démarrer le frontend
```bash
cd pinnokio_app
reflex run
```

### 3. Vérifier dans le navigateur

1. Se connecter à l'app
2. Naviguer vers la page Expenses
3. Ouvrir la console navigateur (F12)
4. Vérifier les logs:
   ```
   📚 [EXPENSES] Début du chargement depuis backend...
   ✅ [EXPENSES] Reçu 42 dépenses depuis backend (source: cache)
   ```

5. Vérifier les logs backend:
   ```
   🔍 [FIREBASE_CACHE] Tentative de récupération: cache:uid:cid:expenses:details
   ✅ [FIREBASE_CACHE] HIT: cache:uid:cid:expenses:details | Items: 42
   ```

---

## ✅ Checklist de validation

### Backend
- [ ] Serveur démarre sans erreurs
- [ ] FIREBASE_CACHE.get_expenses retourne ok=true
- [ ] FIREBASE_CACHE.get_ap_documents retourne ok=true
- [ ] FIREBASE_CACHE.get_bank_transactions retourne ok=true
- [ ] FIREBASE_CACHE.get_approval_pendinglist retourne ok=true
- [ ] FIREBASE_CACHE.get_mandate_snapshot retourne ok=true
- [ ] DRIVE_CACHE.get_documents retourne ok=true (ou oauth_error)
- [ ] Logs montrent HIT après deuxième appel
- [ ] Redis contient les clés cache:*

### Frontend (après migration)
- [ ] expense_state.py charge les données
- [ ] Logs console montrent "source: cache" au 2ème chargement
- [ ] Pas d'erreurs dans la console navigateur
- [ ] Pas d'import redis_cache_manager dans le fichier migré

---

## 📞 Support

Si vous rencontrez des problèmes:

1. Vérifier les logs backend (terminal où tourne uvicorn)
2. Vérifier les logs frontend (console navigateur F12)
3. Vérifier Redis: `redis-cli KEYS cache:*`
4. Consulter `MIGRATION.md` section "Dépannage"

---

**Bon tests! 🚀**
