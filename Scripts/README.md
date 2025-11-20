# 🛠️ Scripts de Diagnostic et Maintenance

Ce dossier contient des scripts utilitaires pour diagnostiquer et maintenir le microservice listeners.

## 📋 Scripts Disponibles

### `diagnose_websocket.py` - Diagnostic WebSocket

Script complet pour tester la connectivité et la stabilité des WebSockets.

#### Installation des Dépendances

```bash
pip install websockets requests
```

#### Usage

**Test complet (avec stabilité 30s) :**
```bash
python scripts/diagnose_websocket.py --service-url https://your-service.com
```

**Test rapide (sans stabilité) :**
```bash
python scripts/diagnose_websocket.py --service-url http://localhost:8090 --skip-stability
```

**Avec user ID personnalisé :**
```bash
python scripts/diagnose_websocket.py --service-url https://your-service.com --user-id test-user-123
```

**Sauvegarder les résultats :**
```bash
python scripts/diagnose_websocket.py --service-url https://your-service.com --output results.json
```

#### Tests Exécutés

1. ✅ **Health Check HTTP** - Vérifie que le service répond
2. ✅ **Métriques WebSocket** - Récupère les stats de déconnexion
3. ✅ **Connexion WebSocket** - Teste la connexion de base
4. ✅ **Reconnexion Rapide** - Teste la race condition (délai 5s)
5. ✅ **Stabilité WebSocket** - Maintient la connexion 30s
6. ✅ **Ping/Pong** - Vérifie le mécanisme de keepalive

#### Exemple de Sortie

```
[12:34:56] 🔬 Début du diagnostic WebSocket
[12:34:56] 🌐 Service: https://your-service.com
[12:34:56] 👤 User ID: diagnostic-user

[12:34:56] 🏥 Test 1/6: Health Check HTTP
[12:34:56] ✅ Service UP - Listeners: 5

[12:34:56] 📊 Test 2/6: Métriques WebSocket
[12:34:57] ✅ Métriques disponibles - 3 utilisateurs trackés

[12:34:57] 🔌 Test 3/6: Connexion WebSocket
[12:34:58] ✅ Connexion établie
[12:35:00] ✅ Connexion stable après 2 secondes

[12:35:00] 🔄 Test 4/6: Reconnexion Rapide (race condition)
[12:35:00]   📡 Connexion 1...
[12:35:01]   ✅ Connexion 1 établie
[12:35:01]   ⏱️ Attente 1 seconde...
[12:35:02]   📡 Connexion 2 (reconnexion rapide)...
[12:35:04]   ✅ Reconnexion réussie (cleanup devrait être annulé)

[12:35:04] 🕐 Test 5/6: Stabilité WebSocket (30s)
[12:35:04]   ⏳ Connexion établie, maintien pendant 30s...
[12:35:09]   ✅ Connexion stable (5/30s)
[12:35:14]   ✅ Connexion stable (10/30s)
...
[12:35:34] ✅ Connexion maintenue 30.1s sans interruption

[12:35:34] 🏓 Test 6/6: Ping/Pong
[12:35:34]   ⏳ Connexion établie, attente de 3 pings...
[12:35:49] ✅ Pings/Pongs fonctionnent correctement

============================================================
[12:35:49] 📊 RÉSUMÉ DU DIAGNOSTIC
============================================================
[12:35:49] ✅ Tests réussis: 6/6

[12:35:49] ✅ Diagnostic terminé
```

#### Interprétation des Résultats

| Résultat | Signification | Action |
|----------|---------------|--------|
| ✅ 6/6 tests passés | Tout fonctionne correctement | Aucune action |
| ❌ Health Check échoue | Service down ou URL incorrecte | Vérifier service/URL |
| ❌ WS Connection échoue | Problème connexion WebSocket | Vérifier logs backend |
| ❌ Stabilité échoue | Déconnexions fréquentes | Consulter `/ws-metrics` |
| ❌ Ping/Pong échoue | Backend bloqué/surchargé | Vérifier event loop |

## 🚀 Utilisation en Production

### Diagnostic Rapide

```bash
# Test local
python scripts/diagnose_websocket.py --service-url http://localhost:8090 --skip-stability

# Test staging
python scripts/diagnose_websocket.py --service-url https://staging.your-service.com --skip-stability

# Test production (avec rapport)
python scripts/diagnose_websocket.py \
    --service-url https://your-service.com \
    --output diagnostic-$(date +%Y%m%d-%H%M%S).json
```

### CI/CD Integration

Ajoutez à votre pipeline CI/CD :

```yaml
# .github/workflows/test-websocket.yml
name: WebSocket Health Check

on:
  schedule:
    - cron: '0 */6 * * *'  # Toutes les 6 heures
  workflow_dispatch:

jobs:
  diagnose:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: pip install websockets requests
      
      - name: Run diagnostic
        run: |
          python scripts/diagnose_websocket.py \
            --service-url ${{ secrets.SERVICE_URL }} \
            --output diagnostic.json
      
      - name: Upload results
        uses: actions/upload-artifact@v3
        with:
          name: websocket-diagnostic
          path: diagnostic.json
```

### Monitoring Cron (Serveur)

```bash
# Ajouter au crontab (toutes les heures)
0 * * * * cd /path/to/repo && python scripts/diagnose_websocket.py --service-url https://your-service.com --skip-stability >> /var/log/websocket-diagnostic.log 2>&1
```

## 📊 Analyse des Résultats

### Fichier JSON de Sortie

```json
{
  "timestamp": "2025-11-20T12:34:56",
  "service_url": "https://your-service.com",
  "user_id": "diagnostic-user",
  "tests": {
    "http_health": {
      "status": "ok",
      "data": {
        "status": "ok",
        "listeners_count": 5,
        "redis": "ok"
      }
    },
    "ws_connection": {
      "status": "ok",
      "duration": 2
    },
    "ws_stability": {
      "status": "ok",
      "duration": 30.1,
      "requested": 30
    }
  }
}
```

### Automatiser l'Analyse

```python
import json

with open('diagnostic.json') as f:
    results = json.load(f)

# Vérifier si tous les tests ont passé
all_ok = all(
    test.get("status") == "ok" 
    for test in results["tests"].values()
)

if not all_ok:
    print("❌ Certains tests ont échoué, investigation requise")
    # Envoyer alerte (Slack, email, PagerDuty, etc.)
else:
    print("✅ Tous les tests ont réussi")
```

## 🔧 Développement de Nouveaux Scripts

### Template de Base

```python
#!/usr/bin/env python3
"""
Description du script
"""
import argparse
import sys

def main():
    parser = argparse.ArgumentParser(description="Description")
    parser.add_argument("--option", help="Description de l'option")
    args = parser.parse_args()
    
    # Votre logique ici
    print("Script exécuté avec succès")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️  Interrompu par l'utilisateur")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Erreur: {e}")
        sys.exit(1)
```

## 📚 Ressources

- [Documentation WebSocket](../WEBSOCKET_FIXES.md)
- [Checklist de Déploiement](../DEPLOYMENT_CHECKLIST.md)
- [Guide de Troubleshooting](../docs/troubleshooting.md)

## 🆘 Support

En cas de problème avec les scripts :
1. Vérifiez que les dépendances sont installées
2. Vérifiez l'URL du service
3. Consultez les logs du service backend
4. Ouvrez une issue sur GitHub avec les résultats du diagnostic

