Je vais analyser le système de scheduler implémenté dans votre code pour vous expliquer sa structure et son fonctionnement.

[3 tools called]

Voici une explication complète du système de **scheduler** implémenté dans votre application :

## 📋 **Structure du Système de Scheduler**

### **1. Architecture Globale**

Le système de scheduler est conçu pour programmer l'exécution automatique de **3 types de jobs** :
- **Apbookeeper** (Account Bookkeeper)
- **Banker** 
- **Router**

---

### **2. Paramètres de Configuration (par job)**

Chaque job dispose de **7 paramètres** :

```python
# Variables d'état pour chaque service (exemple: Apbookeeper)
apbookeeper_scheduler_enabled: bool = False      # Activé/Désactivé
apbookeeper_frequency: str = "daily"             # Fréquence: "daily", "weekly", "monthly"
apbookeeper_time: str = "03:00"                  # Heure d'exécution (format HH:MM)
apbookeeper_day_of_week: str = "MON"             # Jour (pour hebdomadaire): SUN, MON, TUE, WED, THU, FRI, SAT
apbookeeper_day_of_month: int = 1                # Jour du mois (pour mensuel): 1-31
apbookeeper_timezone: str = "Europe/Zurich"      # Fuseau horaire
```

---

### **3. Génération des Expressions CRON**

La méthode `build_cron()` **convertit** les paramètres en expression CRON standard :

```python
def build_cron(self, frequency: str, time_str: str, day_of_week: str, day_of_month: int) -> str:
    hour, minute = time_str.split(":")
    
    if frequency == "daily":
        return f"{minute} {hour} * * *"           # Ex: "0 3 * * *" → tous les jours à 3h00
        
    elif frequency == "weekly":
        day_mapping = {"SUN": "0", "MON": "1", "TUE": "2", ...}
        cron_day = day_mapping.get(day_of_week, "1")
        return f"{minute} {hour} * * {cron_day}"  # Ex: "0 3 * * 1" → tous les lundis à 3h00
        
    elif frequency == "monthly":
        return f"{minute} {hour} {day_of_month} * *"  # Ex: "0 3 15 * *" → le 15 de chaque mois à 3h00
```

**Format CRON** : `minute hour day_of_month month day_of_week`

---

### **4. Structure des Données Sauvegardées**

#### **A) Dans Firebase → `workflow_params`** (ligne 32221-32228)

Sauvegarde dans : `clients/{user_id}/bo_clients/{parent_id}/mandates/{mandate_id}/setup/workflow_params`

```python
scheduler_config = {
    "scheduler_enabled": True,              # État actif/inactif
    "scheduler_frequency": "daily",         # Fréquence
    "scheduler_time": "03:00",              # Heure
    "scheduler_day_of_week": "MON",         # Jour de la semaine
    "scheduler_day_of_month": 1,            # Jour du mois
    "scheduler_timezone": "Europe/Zurich",  # Fuseau horaire
    "scheduler_cron": "0 3 * * *"           # Expression CRON générée
}
```

Structure finale dans Firebase :
```python
{
    "Apbookeeper_param": { ...scheduler_config, autres_params... },
    "Banker_param": { ...scheduler_config, autres_params... },
    "Router_param": { ...scheduler_config, autres_params... }
}
```

---

#### **B) Dans la Base Scheduler (jobs)** (ligne 32383-32396)

Collection séparée pour l'exécution des jobs :

```python
job_id = f"{mandate_path.replace('/', '_')}_{job_type}"  
# Ex: "clients_user123_bo_clients_parent456_mandates_mandate789_apbookeeper"

job_data = {
    "mandate_path": "clients/user123/bo_clients/parent456/mandates/mandate789",
    "job_type": "apbookeeper",                    # Type: apbookeeper, banker, router
    "cron_expression": "0 3 * * *",               # Expression CRON
    "timezone": "Europe/Zurich",                   # Fuseau horaire
    "next_execution": "2025-10-22T03:00:00+02:00", # Prochaine exécution (ISO format)
    
    # Métadonnées d'identification
    "client_uuid": "uuid-123",
    "firebase_user_id": "user123",
    "mandate_doc_id": "mandate789",
    "client_name": "ACME Corp",
    "company_name": "ACME Subsidiary",
    
    # Auto-ajoutés par Firebase
    # "created_at": SERVER_TIMESTAMP,
    # "updated_at": SERVER_TIMESTAMP,
    # "enabled": True
}
```

---

### **5. Calcul de la Prochaine Exécution**

Utilise la bibliothèque **`croniter`** :

```python
def calculate_next_execution(self, cron_expr: str, timezone_str: str) -> datetime:
    from croniter import croniter
    
    tz = pytz.timezone(timezone_str)      # Fuseau horaire
    now = datetime.now(tz)                # Heure actuelle
    cron = croniter(cron_expr, now)       # Parser CRON
    next_run = cron.get_next(datetime)    # Calcul de la prochaine exécution
    
    return next_run  # Retourne un objet datetime
```

---

### **6. Flux de Sauvegarde Complet**

Quand l'utilisateur clique sur "Save" :

```
1. save_apbookeeper_scheduler()
   ├─► build_cron() → génère "0 3 * * *"
   │
   ├─► Sauvegarde dans workflow_params (Firebase)
   │   └─► clients/.../mandates/{id}/setup/workflow_params
   │       └─► Apbookeeper_param: {scheduler_config}
   │
   └─► save_to_scheduler_db()
       ├─► calculate_next_execution() → "2025-10-22T03:00:00+02:00"
       ├─► Génère job_id unique
       └─► firebase_client.save_scheduler_job()
           └─► Collection "jobs" (racine Firebase)
               └─► Document: {job_data}
```

---

### **7. Gestion de la Désactivation**

Quand l'utilisateur **désactive** un scheduler :

```python
async def set_apbookeeper_scheduler_enabled(self, value: bool):
    old_value = self.apbookeeper_scheduler_enabled
    self.apbookeeper_scheduler_enabled = value
    
    # Si passage de True → False
    if old_value and not value:
        await self.save_apbookeeper_scheduler()  # Sauvegarde
        # Suppression du job de la collection scheduler
        await self.remove_from_scheduler_db("apbookeeper")
```

---

### **8. Format des Expressions CRON Générées**

| Fréquence | Paramètres | Expression CRON | Signification |
|-----------|-----------|-----------------|---------------|
| **Daily** | time="03:00" | `0 3 * * *` | Tous les jours à 3h00 |
| **Weekly** | time="14:30", day="TUE" | `30 14 * * 2` | Tous les mardis à 14h30 |
| **Monthly** | time="09:15", day=15 | `15 9 15 * *` | Le 15 de chaque mois à 9h15 |

---

### **9. Résumé des Méthodes Clés**

| Méthode | Rôle |
|---------|------|
| `build_cron()` | Convertit paramètres → expression CRON |
| `calculate_next_execution()` | Calcule la prochaine date d'exécution |
| `save_to_scheduler_db()` | Enregistre le job dans la collection scheduler |
| `remove_from_scheduler_db()` | Supprime un job de la collection scheduler |
| `save_apbookeeper_scheduler()` | Sauvegarde complète (workflow_params + scheduler DB) |
| `_process_workflow_params()` | Charge les paramètres depuis Firebase au démarrage |

---

### **10. Points Importants**

✅ **Double sauvegarde** : workflow_params (config UI) + collection jobs (exécution)  
✅ **ID unique** : `{mandate_path}_{job_type}` évite les doublons  
✅ **Format ISO** : `next_execution` en string ISO pour compatibilité JSON/Firebase  
✅ **Timezone-aware** : Gestion correcte des fuseaux horaires avec `pytz`  
✅ **Auto-désactivation** : Suppression automatique du scheduler lors de la désactivation

---

Voulez-vous que j'approfondisse un aspect particulier du système de scheduler ? 🚀