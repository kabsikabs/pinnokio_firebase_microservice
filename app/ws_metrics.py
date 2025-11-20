"""
Module pour collecter les métriques WebSocket.
Permet de suivre les connexions, déconnexions et diagnostiquer les problèmes.
"""

import logging
import time
from typing import Dict, Optional
from collections import defaultdict
import threading

logger = logging.getLogger("listeners.ws_metrics")


class WebSocketMetrics:
    """Collecteur de métriques WebSocket pour diagnostic."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._disconnects: Dict[str, int] = defaultdict(int)
        self._last_disconnect_time: Dict[str, float] = {}
        self._disconnect_reasons: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        
    def record_disconnect(self, uid: str, reason: str = "unknown") -> None:
        """Enregistre une déconnexion WebSocket."""
        with self._lock:
            self._disconnects[uid] += 1
            self._last_disconnect_time[uid] = time.time()
            self._disconnect_reasons[uid][reason] += 1
            
            # Log si déconnexions fréquentes (> 3 en cache)
            if self._disconnects[uid] > 3:
                logger.warning(
                    "ws_metrics_frequent_disconnect uid=%s total=%s reasons=%s",
                    uid, self._disconnects[uid], dict(self._disconnect_reasons[uid])
                )
    
    def get_disconnect_count(self, uid: str) -> int:
        """Retourne le nombre de déconnexions pour un utilisateur."""
        with self._lock:
            return self._disconnects.get(uid, 0)
    
    def get_last_disconnect_time(self, uid: str) -> Optional[float]:
        """Retourne le timestamp de la dernière déconnexion."""
        with self._lock:
            return self._last_disconnect_time.get(uid)
    
    def get_disconnect_reasons(self, uid: str) -> Dict[str, int]:
        """Retourne les raisons de déconnexion pour un utilisateur."""
        with self._lock:
            return dict(self._disconnect_reasons.get(uid, {}))
    
    def clear_user_metrics(self, uid: str) -> None:
        """Nettoie les métriques pour un utilisateur (après reconnexion stable)."""
        with self._lock:
            self._disconnects.pop(uid, None)
            self._last_disconnect_time.pop(uid, None)
            self._disconnect_reasons.pop(uid, None)
    
    def get_summary(self) -> Dict:
        """Retourne un résumé des métriques."""
        with self._lock:
            return {
                "total_users_tracked": len(self._disconnects),
                "top_disconnects": sorted(
                    [(uid, count) for uid, count in self._disconnects.items()],
                    key=lambda x: x[1],
                    reverse=True
                )[:10],
                "all_reasons": {
                    uid: dict(reasons) 
                    for uid, reasons in list(self._disconnect_reasons.items())[:10]
                }
            }


# Singleton global
_metrics = WebSocketMetrics()


def record_ws_disconnect(uid: str, reason: str = "unknown") -> None:
    """Enregistre une déconnexion WebSocket."""
    _metrics.record_disconnect(uid, reason)


def get_ws_metrics() -> WebSocketMetrics:
    """Retourne l'instance du collecteur de métriques."""
    return _metrics

