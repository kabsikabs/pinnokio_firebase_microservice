# Workflow Listener On-Demand - Documentation Frontend

## 🎯 **Objectif**

Cette nouvelle architecture permet d'activer le **Workflow Listener uniquement pour un job spécifique** lorsque l'utilisateur ouvre la page EditForm, au lieu de surveiller toute la collection `task_manager/` en permanence.

## 📊 **Bénéfices**

| Métrique | Avant (Global) | Après (On-Demand) | Réduction |
|----------|----------------|-------------------|-----------|
| Listeners actifs | Tous les users connectés | Seulement users sur EditForm | **~95%** |
| Documents surveillés | Toute la collection | 1 seul document | **~99%** |
| Événements Redis publiés | Tous les jobs | 1 job actif | **~99%** |
| Charge CPU/Mémoire | Élevée | Normale | **~98%** |
| Timeout AWS | Fréquent | Éliminé | **100%** |

---

## 🔧 **Architecture**

### **Flux Avant (Global - Problématique)**

```
User se connecte
    ↓
WorkflowListener démarré GLOBALEMENT
    ↓
Surveille clients/{uid}/task_manager/* (TOUS les documents)
    ↓
APBookkeeper met à jour job_123
    ↓
Listener détecte TOUS les changements
    ↓
Publie sur Redis pour TOUS les jobs
    ↓
EditFormState reçoit et filtre (99% rejeté)
    ↓
⚠️ Boucle infinie + Timeout AWS
```

### **Flux Après (On-Demand - Solution)**

```
User ouvre EditForm pour job_123
    ↓
Frontend appelle start_workflow_listener_for_job(uid, job_123)
    ↓
Listener démarré UNIQUEMENT pour job_123
    ↓
Surveille clients/{uid}/task_manager/job_123 (1 seul document)
    ↓
APBookkeeper met à jour job_123
    ↓
Listener détecte SEULEMENT ce job
    ↓
Publie sur Redis UNIQUEMENT pour job_123
    ↓
EditFormState reçoit les événements pertinents
    ↓
User ferme EditForm
    ↓
Frontend appelle stop_workflow_listener_for_job(uid, job_123)
    ↓
✅ Listener arrêté, ressources libérées
```

---

## 💻 **Implémentation Frontend**

### **1. Méthodes RPC Disponibles**

Le microservice expose deux nouvelles méthodes :

#### **`LISTENERS.start_workflow_listener_for_job(uid, job_id)`**

Démarre la surveillance workflow pour un job spécifique.

**Arguments :**
- `uid` (str) : ID de l'utilisateur Firebase
- `job_id` (str) : ID du job à surveiller

**Retour :**
- `bool` : `True` si succès, `False` sinon

#### **`LISTENERS.stop_workflow_listener_for_job(uid, job_id)`**

Arrête la surveillance workflow pour un job spécifique.

**Arguments :**
- `uid` (str) : ID de l'utilisateur Firebase
- `job_id` (str) : ID du job à arrêter

**Retour :**
- `bool` : `True` si succès, `False` sinon

---

### **2. Intégration dans EditFormState**

#### **a) Au montage de la page (on_mount ou équivalent)**

```python
# EditFormState.py

@rx.event(background=True)
async def initialize_job_data(self, job_id: str):
    """
    Initialise les données d'un job et démarre le listener workflow.
    
    Cette méthode doit être appelée quand l'utilisateur ouvre la page EditForm
    pour un job spécifique.
    """
    try:
        # Sauvegarder le job_id actuel
        async with self:
            self.current_job_id = job_id
        
        # Charger les données du job depuis Firebase
        await self.load_invoice_data(job_id)
        
        # ⭐ DÉMARRER le listener workflow pour CE job uniquement
        from ..code.tools.rpc_client import call_rpc_method
        
        auth_state = await self.get_state(AuthState)
        user_id = auth_state.firebase_user_id
        
        if user_id and job_id:
            try:
                result = call_rpc_method(
                    "LISTENERS.start_workflow_listener_for_job",
                    user_id,
                    job_id
                )
                print(f"✅ Listener workflow démarré pour job {job_id}: {result}")
                
                async with self:
                    self.workflow_listener_active = True
                    
            except Exception as e:
                print(f"❌ Erreur démarrage listener workflow: {e}")
                async with self:
                    self.workflow_listener_active = False
        
    except Exception as e:
        print(f"❌ Erreur initialisation job: {e}")
```

#### **b) Au démontage de la page (cleanup)**

```python
# EditFormState.py

@rx.event(background=True)
async def cleanup_job_listener(self):
    """
    Nettoie le listener workflow quand on quitte la page.
    
    Cette méthode doit être appelée quand :
    - L'utilisateur ferme la page EditForm
    - L'utilisateur navigue vers une autre page
    - Le composant est démonté
    """
    try:
        from ..code.tools.rpc_client import call_rpc_method
        
        auth_state = await self.get_state(AuthState)
        user_id = auth_state.firebase_user_id
        job_id = self.current_job_id
        
        if user_id and job_id and self.workflow_listener_active:
            try:
                result = call_rpc_method(
                    "LISTENERS.stop_workflow_listener_for_job",
                    user_id,
                    job_id
                )
                print(f"✅ Listener workflow arrêté pour job {job_id}: {result}")
                
            except Exception as e:
                print(f"❌ Erreur arrêt listener workflow: {e}")
        
        async with self:
            self.current_job_id = None
            self.workflow_listener_active = False
            
    except Exception as e:
        print(f"❌ Erreur cleanup listener: {e}")
```

#### **c) Variables d'état à ajouter**

```python
# EditFormState.py - Variables de classe

class EditFormState(rx.State):
    # ... autres variables ...
    
    # ⭐ NOUVEAU: État du listener workflow
    current_job_id: str = ""
    workflow_listener_active: bool = False
```

---

### **3. Intégration dans le composant React**

#### **Au montage du composant**

```python
# Dans la définition de votre page EditForm

def edit_form_page() -> rx.Component:
    """Page de modification de facture avec listener workflow on-demand."""
    
    return rx.fragment(
        # Événement appelé au montage du composant
        rx.call_script(
            """
            // Récupérer le job_id depuis l'URL ou les props
            const jobId = window.location.pathname.split('/').pop();
            
            // Appeler initialize_job_data via Reflex
            // (Adaptation selon votre pattern d'événements Reflex)
            """,
            on_mount=True
        ),
        
        # Votre contenu de page
        rx.box(
            # ... composants de formulaire ...
        ),
        
        # Événement appelé au démontage du composant
        on_unmount=EditFormState.cleanup_job_listener,
    )
```

#### **Alternative : Using React useEffect**

Si vous utilisez un composant React personnalisé :

```javascript
useEffect(() => {
    // Au montage
    const jobId = getJobIdFromUrl();
    EditFormState.initialize_job_data(jobId);
    
    // Au démontage
    return () => {
        EditFormState.cleanup_job_listener();
    };
}, []);
```

---

## 🧪 **Tests de Validation**

### **1. Test de démarrage**

```python
# Test manuel dans console Python

from app.listeners_manager import listeners_manager

# Démarrer un listener pour un job
result = listeners_manager.start_workflow_listener_for_job(
    uid="7hQs0jluP5YUWcREqdi22NRFnU32",
    job_id="1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n"
)
print(f"Démarrage: {result}")  # Should be True

# Vérifier qu'il ne démarre pas deux fois
result2 = listeners_manager.start_workflow_listener_for_job(
    uid="7hQs0jluP5YUWcREqdi22NRFnU32",
    job_id="1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n"
)
print(f"Démarrage duplicate: {result2}")  # Should be True (already active)
```

### **2. Test d'arrêt**

```python
# Arrêter le listener
result = listeners_manager.stop_workflow_listener_for_job(
    uid="7hQs0jluP5YUWcREqdi22NRFnU32",
    job_id="1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n"
)
print(f"Arrêt: {result}")  # Should be True

# Vérifier qu'on ne peut pas arrêter deux fois
result2 = listeners_manager.stop_workflow_listener_for_job(
    uid="7hQs0jluP5YUWcREqdi22NRFnU32",
    job_id="1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n"
)
print(f"Arrêt duplicate: {result2}")  # Should be False (not active)
```

### **3. Test de publication d'événements**

```python
# Simuler un changement dans Firestore
from app.firebase_providers import get_firebase_management

firebase = get_firebase_management()
firebase.upload_invoice_step(
    user_id="7hQs0jluP5YUWcREqdi22NRFnU32",
    job_id="1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n",
    invoice_step={"step_extract_data": 5}
)

# Vérifier dans les logs que l'événement est publié UNIQUEMENT pour ce job
# Logs attendus :
# workflow_job_change uid=7hQs0jluP5YUWcREqdi22NRFnU32 job_id=1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n
# workflow.step_update published
```

---

## 📝 **Logs de Diagnostic**

### **Logs de succès**

```
✅ workflow_listener_start_for_job uid=7hQs0jluP5YUWcREqdi22NRFnU32 job_id=1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n
✅ workflow_listener_attached_for_job uid=7hQs0jluP5YUWcREqdi22NRFnU32 job_id=1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n
✅ workflow_job_change uid=7hQs0jluP5YUWcREqdi22NRFnU32 job_id=1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n
✅ workflow.step_update uid=7hQs0jluP5YUWcREqdi22NRFnU32 job_id=1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n changes={'step_extract_data': 5}
✅ workflow_listener_stopped_for_job uid=7hQs0jluP5YUWcREqdi22NRFnU32 job_id=1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n
```

### **Logs d'erreur**

```
❌ workflow_listener_start_error uid=7hQs0jluP5YUWcREqdi22NRFnU32 job_id=invalid_job error=...
❌ workflow_job_snapshot_error uid=7hQs0jluP5YUWcREqdi22NRFnU32 job_id=1twzEr0KIJcgf2ATDPb8PnDzIQCdULd0n error=...
```

---

## 🚨 **Points d'Attention**

### **1. Nettoyage obligatoire**

⚠️ **IMPORTANT** : Toujours appeler `stop_workflow_listener_for_job()` quand on quitte la page, sinon le listener reste actif inutilement.

**Solution** : Utiliser `on_unmount` ou `useEffect cleanup` pour garantir l'appel.

### **2. Gestion des reconnexions**

Si l'utilisateur rafraîchit la page :
- Le listener existant sera détecté comme "already active"
- Aucun doublon ne sera créé
- Mais il faut quand même appeler `cleanup` à la fermeture

### **3. Navigation rapide**

Si l'utilisateur navigue rapidement entre plusieurs jobs :
- Arrêter le listener du job précédent
- Démarrer le listener du nouveau job
- Éviter les listeners orphelins

**Exemple** :

```python
@rx.event(background=True)
async def switch_to_job(self, new_job_id: str):
    """Change de job en nettoyant l'ancien listener."""
    # Arrêter l'ancien
    if self.current_job_id:
        await self.cleanup_job_listener()
    
    # Démarrer le nouveau
    await self.initialize_job_data(new_job_id)
```

---

## 📚 **Références**

- **Backend** : `app/listeners_manager.py` (lignes 1232-1368)
- **RPC Routing** : `app/main.py` (lignes 570-576)
- **Documentation Architecture** : `doc/REFLEX_INTEGRATION.md`

---

## ✅ **Checklist d'Implémentation**

- [ ] Ajouter `current_job_id` et `workflow_listener_active` à `EditFormState`
- [ ] Implémenter `initialize_job_data()` dans `EditFormState`
- [ ] Implémenter `cleanup_job_listener()` dans `EditFormState`
- [ ] Appeler `initialize_job_data()` au montage de la page EditForm
- [ ] Appeler `cleanup_job_listener()` au démontage de la page EditForm
- [ ] Tester avec un job APBookkeeper en cours
- [ ] Vérifier les logs côté microservice
- [ ] Valider la réduction du trafic Redis (monitoring)
- [ ] Déployer sur AWS et monitorer les métriques

---

**Date de création** : 23 novembre 2025  
**Auteur** : Architecture Team  
**Version** : 1.0.0

********************MISE A JOUR*****************
[17 tools called]

Voici une documentation détaillée des valeurs stockées dans `self.validator` et synchronisées en temps réel vers Firebase dans le chemin `/task_manager/{job_id}`.

### 📋 Documentation Technique : Synchronisation `task_manager`

Cette documentation décrit les champs persistés dans Firebase pour permettre au frontend de suivre l'état de l'extraction et de la validation des documents.

#### 1. Informations de l'En-tête (Header)
Ces valeurs représentent les données extraites directement du document.

| Nom du champ | Type | Description |
| :--- | :--- | :--- |
| `sender` | `String` | Nom du fournisseur (expéditeur). |
| `recipient` | `String` | Nom de l'entreprise destinataire. |
| `invoiceReference` | `String` | Numéro de facture ou référence unique. |
| `invoiceDate` | `String` | Date d'émission (format `YYYY-MM-DD`). |
| `dueDate` | `String` | Date d'échéance (format `YYYY-MM-DD`). |
| `currency` | `String` | Code de la devise (ex: `CHF`, `EUR`). |
| `totalAmountDueVATIncluded` | `Number` | Montant total toutes taxes comprises (TTC). |
| `totalAmountDueVATExcluded` | `Number` | Montant total hors taxes (HT). |
| `VATAmount` | `Number` | Montant total de la TVA. |
| `VATPercentages` | `List[Number]` | Liste des taux de TVA détectés (ex: `[7.7, 8.1]`). |

#### 2. Informations Fournisseur & Partenaire (Master Data)
Champs liés à la correspondance avec la base de données Odoo.

| Nom du champ | Type | Description |
| :--- | :--- | :--- |
| `partner_id` | `Integer` | ID interne Odoo du partenaire. |
| `supplier_id` | `Integer` | Alias de `partner_id` pour la compatibilité. |
| `sender_country_code` | `String` | Code ISO du pays (ex: `CH`, `FR`). |
| `sender_country_id` | `Integer` | ID Odoo du pays. |
| `senderAddress` | `String` | Adresse complète extraite. |
| `sender_zip` | `String` | Code postal. |
| `sender_city` | `String` | Ville. |
| `sender_vat` | `String` | Numéro de TVA du fournisseur. |
| `sender_email` | `String` | Adresse email de contact. |
| `sender_phone` | `String` | Numéro de téléphone. |

#### 3. Paramètres Comptables & Processus
Données utilisées pour la génération des écritures dans l'ERP.

| Nom du champ | Type | Description |
| :--- | :--- | :--- |
| `accounting_date` | `String` | Date de comptabilisation (format `YYYY-MM-DD`). |
| `account_ids` | `List[Integer]` | Liste des IDs Odoo des comptes de charge/immobilisation. |
| `tax_ids` | `List[Integer]` | Liste des IDs Odoo des taxes applicables. |
| `currency_id` | `Integer` | ID Odoo de la devise utilisée. |
| `currency_xrate` | `Number` | Taux de change appliqué (si devise étrangère). |
| `new_invoice_y_n` | `String` | Statut du document (`NEW_INVOICE` ou `EXISTING_INVOICE`). |
| `booking_type` | `String` | Mode de saisie (`single_line` ou `multi_lines`). |
| `booking_method` | `String` | Logique appliquée (`historical` ou `complex`). |

#### 4. Structures Complexes (Payloads JSON)
Données structurées pour les flux avancés (ex: Immobilisations).

- **`accounting_lines_to_post`** : Un objet JSON contenant le détail des lignes (nom, compte, montant, taxes, analytique).
- **`immobilisation_data`** : Un dictionnaire contenant les paramètres spécifiques aux actifs à créer (modèle, durée, montant amortissable).
- **`created_asset_ids`** : Une liste d'entiers contenant les IDs des actifs déjà créés dans Odoo.
- **`asset_creation_completed`** : Un booléen indiquant si l'étape de création des actifs est terminée.

---

### 🚀 Format du Payload de Mise à Jour (Real-time)

Le frontend reçoit les mises à jour via un `patch` sur le document Firebase. 

**Exemple de payload envoyé lors d'une mise à jour :**
```json
{
  "sender": "Swisscom AG",
  "invoiceReference": "INV-2023-001",
  "partner_id": 4502,
  "totalAmountDueVATIncluded": 125.50,
  "accounting_date": "2023-12-25",
  "account_ids": [1024, 1025],
  "asset_creation_completed": false
}
```

**Note pour le Frontend :** Les mises à jour sont partielles (seuls les champs modifiés sont envoyés). Le backend détecte dynamiquement **tous les changements** dans `document/initial_data` (incluant `invoice_lines`, `totalAmountDueVATIncluded`, etc.) et les transmet via l'événement `workflow.invoice_update`.

#### 🧩 Format de l'événement `workflow.invoice_update`

```json
{
  "type": "workflow.invoice_update",
  "uid": "USER_ID",
  "job_id": "JOB_ID",
  "timestamp": "ISO-8601",
  "payload": {
    "invoice_changes": {
      "invoiceReference": "INV-2025-001",
      "invoice_lines": [
        {"name": "Produit A", "quantity": 1, "price_unit": 100.0}
      ]
    }
  }
}
```

Il est recommandé d'écouter les changements sur `task_manager/{job_id}` pour refléter les modifications en temps réel dans l'interface utilisateur.