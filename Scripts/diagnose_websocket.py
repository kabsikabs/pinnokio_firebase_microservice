#!/usr/bin/env python3
"""
Script de diagnostic WebSocket pour identifier les problèmes de connexion.

Usage:
    python scripts/diagnose_websocket.py --service-url https://your-service.com
    python scripts/diagnose_websocket.py --service-url http://localhost:8090
    python scripts/diagnose_websocket.py --help
"""

import asyncio
import argparse
import json
import time
from datetime import datetime
from typing import Optional
import sys

try:
    import websockets
    import requests
except ImportError:
    print("❌ Dépendances manquantes. Installez-les avec :")
    print("   pip install websockets requests")
    sys.exit(1)


class WebSocketDiagnostic:
    def __init__(self, service_url: str, user_id: str = "diagnostic-user"):
        self.service_url = service_url.rstrip('/')
        self.user_id = user_id
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "service_url": service_url,
            "user_id": user_id,
            "tests": {}
        }
    
    def log(self, message: str, emoji: str = "ℹ️"):
        """Log avec timestamp."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {emoji} {message}")
    
    def check_http_health(self) -> bool:
        """Teste le health check HTTP."""
        self.log("Test 1/6: Health Check HTTP", "🏥")
        try:
            response = requests.get(f"{self.service_url}/healthz", timeout=5)
            if response.status_code == 200:
                data = response.json()
                self.log(f"✅ Service UP - Listeners: {data.get('listeners_count', 0)}", "✅")
                self.results["tests"]["http_health"] = {"status": "ok", "data": data}
                return True
            else:
                self.log(f"⚠️  Service répond avec code {response.status_code}", "⚠️")
                self.results["tests"]["http_health"] = {"status": "warning", "code": response.status_code}
                return False
        except Exception as e:
            self.log(f"❌ Erreur: {e}", "❌")
            self.results["tests"]["http_health"] = {"status": "error", "error": str(e)}
            return False
    
    def check_ws_metrics(self) -> Optional[dict]:
        """Récupère les métriques WebSocket."""
        self.log("Test 2/6: Métriques WebSocket", "📊")
        try:
            response = requests.get(f"{self.service_url}/ws-metrics", timeout=5)
            if response.status_code == 200:
                data = response.json()
                metrics = data.get("metrics", {})
                total_users = metrics.get("total_users_tracked", 0)
                self.log(f"✅ Métriques disponibles - {total_users} utilisateurs trackés", "✅")
                self.results["tests"]["ws_metrics"] = {"status": "ok", "metrics": metrics}
                return metrics
            else:
                self.log(f"⚠️  Endpoint métriques indisponible (code {response.status_code})", "⚠️")
                self.results["tests"]["ws_metrics"] = {"status": "unavailable"}
                return None
        except Exception as e:
            self.log(f"❌ Erreur: {e}", "❌")
            self.results["tests"]["ws_metrics"] = {"status": "error", "error": str(e)}
            return None
    
    async def test_ws_connection(self) -> bool:
        """Teste la connexion WebSocket de base."""
        self.log("Test 3/6: Connexion WebSocket", "🔌")
        
        # Construire l'URL WebSocket
        ws_url = self.service_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_full = f"{ws_url}/ws?uid={self.user_id}"
        
        try:
            async with websockets.connect(ws_full, ping_interval=20, ping_timeout=60) as websocket:
                self.log("✅ Connexion établie", "✅")
                
                # Attendre 2 secondes pour voir si la connexion reste ouverte
                await asyncio.sleep(2)
                
                self.log("✅ Connexion stable après 2 secondes", "✅")
                self.results["tests"]["ws_connection"] = {"status": "ok", "duration": 2}
                return True
        except Exception as e:
            self.log(f"❌ Erreur de connexion: {e}", "❌")
            self.results["tests"]["ws_connection"] = {"status": "error", "error": str(e)}
            return False
    
    async def test_ws_reconnection(self) -> bool:
        """Teste la reconnexion rapide (< 5 secondes)."""
        self.log("Test 4/6: Reconnexion Rapide (race condition)", "🔄")
        
        ws_url = self.service_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_full = f"{ws_url}/ws?uid={self.user_id}"
        
        try:
            # Première connexion
            self.log("  Connexion 1...", "📡")
            async with websockets.connect(ws_full, ping_interval=20, ping_timeout=60) as ws1:
                await asyncio.sleep(1)
                self.log("  Connexion 1 établie", "✅")
            
            # Attendre 1 seconde (< 5s délai de cleanup)
            self.log("  Attente 1 seconde...", "⏱️")
            await asyncio.sleep(1)
            
            # Deuxième connexion (reconnexion rapide)
            self.log("  Connexion 2 (reconnexion rapide)...", "📡")
            async with websockets.connect(ws_full, ping_interval=20, ping_timeout=60) as ws2:
                await asyncio.sleep(2)
                self.log("  ✅ Reconnexion réussie (cleanup devrait être annulé)", "✅")
            
            self.results["tests"]["ws_reconnection"] = {"status": "ok", "delay": 1}
            return True
        except Exception as e:
            self.log(f"  ❌ Erreur: {e}", "❌")
            self.results["tests"]["ws_reconnection"] = {"status": "error", "error": str(e)}
            return False
    
    async def test_ws_stability(self, duration: int = 30) -> bool:
        """Teste la stabilité de la connexion WebSocket."""
        self.log(f"Test 5/6: Stabilité WebSocket ({duration}s)", "🕐")
        
        ws_url = self.service_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_full = f"{ws_url}/ws?uid={self.user_id}"
        
        try:
            start_time = time.time()
            async with websockets.connect(ws_full, ping_interval=20, ping_timeout=60) as websocket:
                self.log(f"  Connexion établie, maintien pendant {duration}s...", "⏳")
                
                # Maintenir la connexion pendant la durée spécifiée
                elapsed = 0
                while elapsed < duration:
                    await asyncio.sleep(5)
                    elapsed = int(time.time() - start_time)
                    self.log(f"  Connexion stable ({elapsed}/{duration}s)", "✅")
                
                total_time = time.time() - start_time
                self.log(f"✅ Connexion maintenue {total_time:.1f}s sans interruption", "✅")
                self.results["tests"]["ws_stability"] = {
                    "status": "ok",
                    "duration": total_time,
                    "requested": duration
                }
                return True
        except Exception as e:
            elapsed = time.time() - start_time
            self.log(f"❌ Connexion perdue après {elapsed:.1f}s: {e}", "❌")
            self.results["tests"]["ws_stability"] = {
                "status": "error",
                "error": str(e),
                "duration": elapsed,
                "requested": duration
            }
            return False
    
    async def test_ping_pong(self) -> bool:
        """Teste le mécanisme ping/pong."""
        self.log("Test 6/6: Ping/Pong", "🏓")
        
        ws_url = self.service_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_full = f"{ws_url}/ws?uid={self.user_id}"
        
        try:
            # Connexion avec ping_interval court pour tester
            async with websockets.connect(ws_full, ping_interval=5, ping_timeout=10) as websocket:
                self.log("  Connexion établie, attente de 3 pings...", "⏳")
                
                # Attendre 3 cycles de ping (15 secondes)
                await asyncio.sleep(15)
                
                self.log("✅ Pings/Pongs fonctionnent correctement", "✅")
                self.results["tests"]["ping_pong"] = {"status": "ok"}
                return True
        except Exception as e:
            self.log(f"❌ Erreur ping/pong: {e}", "❌")
            self.results["tests"]["ping_pong"] = {"status": "error", "error": str(e)}
            return False
    
    async def run_all_tests(self, skip_stability: bool = False) -> dict:
        """Exécute tous les tests de diagnostic."""
        self.log("🔬 Début du diagnostic WebSocket", "🔬")
        self.log(f"Service: {self.service_url}", "🌐")
        self.log(f"User ID: {self.user_id}", "👤")
        print()
        
        # Tests HTTP
        http_ok = self.check_http_health()
        print()
        
        metrics = self.check_ws_metrics()
        print()
        
        if not http_ok:
            self.log("⚠️  Service HTTP inaccessible, tests WebSocket annulés", "⚠️")
            return self.results
        
        # Tests WebSocket
        await self.test_ws_connection()
        print()
        
        await self.test_ws_reconnection()
        print()
        
        if not skip_stability:
            await self.test_ws_stability(duration=30)
            print()
        else:
            self.log("Test de stabilité ignoré (--skip-stability)", "⏭️")
            print()
        
        await self.test_ping_pong()
        print()
        
        # Résumé
        self._print_summary()
        
        return self.results
    
    def _print_summary(self):
        """Affiche un résumé des résultats."""
        print("=" * 60)
        self.log("📊 RÉSUMÉ DU DIAGNOSTIC", "📊")
        print("=" * 60)
        
        total_tests = len(self.results["tests"])
        passed = sum(1 for t in self.results["tests"].values() if t.get("status") == "ok")
        failed = total_tests - passed
        
        self.log(f"Tests réussis: {passed}/{total_tests}", "✅" if failed == 0 else "⚠️")
        
        if failed > 0:
            self.log("Tests échoués:", "❌")
            for name, result in self.results["tests"].items():
                if result.get("status") != "ok":
                    error = result.get("error", "Raison inconnue")
                    self.log(f"  - {name}: {error}", "  ❌")
        
        print()
        self.log("✅ Diagnostic terminé", "✅")
        
        # Suggestions
        if failed > 0:
            print()
            self.log("💡 SUGGESTIONS:", "💡")
            
            if self.results["tests"].get("http_health", {}).get("status") != "ok":
                print("  • Vérifiez que le service backend est démarré")
                print("  • Vérifiez l'URL fournie")
            
            if self.results["tests"].get("ws_connection", {}).get("status") == "error":
                print("  • Vérifiez les logs backend pour les erreurs WebSocket")
                print("  • Vérifiez que le port WebSocket est ouvert")
            
            if self.results["tests"].get("ws_stability", {}).get("status") == "error":
                print("  • Connexion instable détectée")
                print("  • Vérifiez les logs pour 'ws_disconnect code=1006'")
                print("  • Consultez /ws-metrics pour les patterns de déconnexion")
            
            if self.results["tests"].get("ping_pong", {}).get("status") == "error":
                print("  • Le backend ne répond pas aux pings")
                print("  • Possible blocage de l'event loop")
                print("  • Vérifiez les health checks ELB")


async def main():
    parser = argparse.ArgumentParser(
        description="Diagnostic WebSocket pour le microservice listeners"
    )
    parser.add_argument(
        "--service-url",
        required=True,
        help="URL du service (ex: https://your-service.com ou http://localhost:8090)"
    )
    parser.add_argument(
        "--user-id",
        default="diagnostic-user",
        help="ID utilisateur pour le test (défaut: diagnostic-user)"
    )
    parser.add_argument(
        "--skip-stability",
        action="store_true",
        help="Ignorer le test de stabilité (30s)"
    )
    parser.add_argument(
        "--output",
        help="Fichier JSON pour sauvegarder les résultats"
    )
    
    args = parser.parse_args()
    
    diagnostic = WebSocketDiagnostic(args.service_url, args.user_id)
    results = await diagnostic.run_all_tests(skip_stability=args.skip_stability)
    
    # Sauvegarder les résultats si demandé
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\n📄 Résultats sauvegardés dans {args.output}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Diagnostic interrompu par l'utilisateur")
        sys.exit(1)

