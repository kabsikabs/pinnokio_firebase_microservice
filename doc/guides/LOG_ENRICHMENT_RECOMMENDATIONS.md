# Recommandations d'Enrichissement des Logs

Ce document présente les recommandations pour enrichir les logs envoyés via `send_message_log` dans les applications métier Router et Banker, afin que l'agent principal puisse mieux assister l'utilisateur.

## Principe Fondamental

**RÈGLE IMPORTANTE**: Tous les logs métier doivent être envoyés via `send_message_log` avec l'argument `text=` UNIQUEMENT. Ne jamais utiliser `card=` ou `cmmd=` pour les logs métier descriptifs. Les logs sont destinés à alimenter l'historique de l'agent principal pour qu'il puisse expliquer à l'utilisateur ce qui se passe.

**FOCUS MÉTIER**: Les logs doivent expliquer la logique métier et les décisions prises, JAMAIS les détails techniques (noms de méthodes, variables, etc.).

---

## 1. ROUTER (new_router.py) - Enrichissement des Logs

### 1.1 Au Début du Traitement d'un Document

**Emplacement**: Méthode `process_and_send_data()`, après `first_elements = self.prepare_document_data()`

**Log actuel**: Aucun log explicite au début

**Log enrichi à ajouter**:
```python
# Après la préparation des données du document
content = f"""Début du traitement du document '{first_elements['file_name_wo_ext']}'.
Type de fichier: {file_extension}
Taille: {file_size_info if available}

Le système va maintenant procéder à l'extraction du contenu, puis à l'analyse pour déterminer le département approprié."""

logger_message = self.audit_agent_loggeur(content=content, step_process='document_processing_start')
self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)
```

### 1.2 Après Extraction du Contenu

**Emplacement**: Après l'extraction réussie (PDF/image/texte) dans `process_and_send_data()`

**Log actuel**: Logs techniques uniquement (print statements)

**Log enrichi à ajouter**:
```python
# Après extraction réussie du contenu
content_preview = documents[:200] + "..." if len(documents) > 200 else documents

content = f"""Extraction du contenu terminée avec succès.
Type d'extraction: {'OCR depuis PDF' if pdf_extraction else 'Vision IA' if vision_used else 'Texte direct'}
Longueur du contenu extrait: {len(documents)} caractères

Aperçu: {content_preview}

Prochaine étape: Génération d'un résumé du document."""

logger_message = self.audit_agent_loggeur(content=content, step_process='content_extraction_complete')
self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)
```

### 1.3 Après Génération du Résumé

**Emplacement**: Après `resume = self.antho_router_manager.process_tool_use(...)` (ligne ~1651)

**Log actuel**: Aucun log

**Log enrichi à ajouter**:
```python
# Après génération du résumé
if resume and 'resume' in resume:
    resume_text = resume['resume']
    content = f"""Résumé du document généré par l'intelligence artificielle:

"{resume_text}"

Ce résumé sera utilisé pour classifier le document dans le bon département.
Prochaine étape: Identification de l'année fiscale."""

    logger_message = self.audit_agent_loggeur(content=content, step_process='resume_generation_complete')
    self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)
```

### 1.4 Après Identification de l'Année Fiscale

**Emplacement**: Après `year = self.antho_router_manager.process_tool_use(...)` (ligne ~1664)

**Log actuel**: Aucun log

**Log enrichi à ajouter**:
```python
# Après identification de l'année fiscale
if year and 'fiscal_year' in year:
    fiscal_year = year.get('fiscal_year')
    content = f"""Année fiscale identifiée: {fiscal_year}

Le document sera classé dans la structure de l'exercice comptable {fiscal_year}.
Prochaine étape: Classification par département métier."""

    logger_message = self.audit_agent_loggeur(content=content, step_process='fiscal_year_identified')
    self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)
```

### 1.5 Après Sélection du Département (Amélioration du log existant)

**Emplacement**: Ligne ~1936 (déjà existant mais à améliorer)

**Log actuel**:
```python
content = f"""Merci de communiquer à l'utilisateur les éléments recoltés"""
```

**Log enrichi à remplacer par**:
```python
# Après sélection du département
selected_service_name = service  # ex: "INVOICES", "EXPENSES", etc.
selected_motivation = selected_service_text.get('motivation', 'Non spécifié')

# Traduction métier des noms de départements
department_names = {
    'INVOICES': 'Factures fournisseurs',
    'EXPENSES': 'Notes de frais',
    'BANK_CASH': 'Opérations bancaires et trésorerie',
    'HR': 'Ressources humaines',
    'TAXES': 'Documents fiscaux',
    'LETTERS': 'Correspondances officielles',
    'CONTRATS': 'Contrats',
    'FINANCIAL_STATEMENT': 'États financiers'
}

department_friendly = department_names.get(selected_service_name, selected_service_name)

content = f"""Classification terminée - Département sélectionné: {department_friendly}

Justification de l'affectation:
{selected_motivation}

{'Prochaine étape: Transmission au service de comptabilité automatisée pour traitement.' if selected_service_name == 'INVOICES' else 'Prochaine étape: Classement automatique dans la structure Google Drive appropriée.'}"""

logger_message = self.audit_agent_loggeur(content=content, step_process='department_classification_complete')
self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)
```

### 1.6 Pendant le Workflow de Classement Drive

**Emplacement**: Méthode `_file_management_workflow()`, à plusieurs points clés

**Logs à ajouter**:

**a) Au début du workflow:**
```python
content = f"""Début du classement automatique du document dans Google Drive.

Le système va maintenant:
1. Explorer la structure de dossiers existante pour le département {department_friendly}
2. Créer les sous-dossiers nécessaires si besoin
3. Déplacer le document au bon emplacement
4. Renommer le fichier si nécessaire pour plus de clarté"""

logger_message = self.audit_agent_loggeur(content=content, step_process='drive_filing_start')
self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)
```

**b) Lors de l'utilisation des outils Drive (enrichir les logs existants):**
```python
# Exemple pour CREATE_FOLDER
content = f"""Création d'un nouveau dossier dans la structure: "{folder_name}"
Emplacement: {parent_folder_path}

Ce dossier permettra d'organiser les documents de type {document_category}."""

logger_message = self.audit_agent_loggeur(content=content, step_process='drive_folder_creation')
self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)

# Exemple pour MOVE_FILE
content = f"""Déplacement du document vers son emplacement final.
Dossier de destination: {destination_folder_name}
Chemin complet: {full_folder_path}

Le document est maintenant correctement archivé."""

logger_message = self.audit_agent_loggeur(content=content, step_process='drive_file_move')
self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)

# Exemple pour RENAME_FILE
content = f"""Renommage du document pour améliorer la clarté.
Ancien nom: {old_name}
Nouveau nom: {new_name}

Ce nom facilite l'identification du document dans les archives."""

logger_message = self.audit_agent_loggeur(content=content, step_process='drive_file_rename')
self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)
```

**c) À la fin du workflow (succès):**
```python
content = f"""Classement automatique terminé avec succès !

Le document "{file_name}" a été archivé dans:
📁 Département: {department_friendly}
📁 Sous-dossier: {subfolder_path}
📁 Année fiscale: {fiscal_year}

Le document est maintenant accessible dans votre structure Google Drive organisée."""

logger_message = self.audit_agent_loggeur(content=content, step_process='drive_filing_complete')
self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)
```

**d) En cas de placement dans "doc_to_do" (révision manuelle):**
```python
content = f"""Le système a identifié une ambiguïté dans le classement du document.

Par précaution, le document a été placé dans le dossier "Documents à réviser" pour validation manuelle.

Raison: {uncertainty_reason}
Action requise: Veuillez vérifier le document et le déplacer manuellement vers le bon emplacement."""

logger_message = self.audit_agent_loggeur(content=content, step_process='manual_review_required')
self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)
```

### 1.7 En Cas d'Erreur

**Emplacement**: Bloc `except` dans `process_and_send_data()` et autres méthodes critiques

**Log enrichi**:
```python
content = f"""Une erreur s'est produite durant le traitement du document.

Type d'erreur: {error_type}
Étape concernée: {step_name}

Le document a été marqué pour révision manuelle. Nos équipes ont été notifiées pour investigation."""

logger_message = self.audit_agent_loggeur(content=content, step_process='processing_error')
self.space_manager.send_message_log(self.collection_name, thread_key=self.drive_to_job_id, text=logger_message)
```

---

## 2. BANKER (pybank.py) - Enrichissement des Logs

### 2.1 Au Début du Traitement d'une Transaction (Amélioration)

**Emplacement**: Méthode `process_transactions()`, au début de la boucle de traitement

**Log actuel**: Log existant ligne ~7518 (mais basique)

**Log enrichi à remplacer**:
```python
# Au début du traitement de chaque transaction
transaction_row = self.df_iterator.get_current_row()
transaction_number = self.df_iterator.get_current_index()
total_transactions = self.df_iterator.get_total_items()

# Extraction des informations clés
move_id = transaction_row.get('id')
date = transaction_row.get('date')
amount = transaction_row.get('amount', 0)
currency = transaction_row.get('currency_id', ['', 'N/A'])[1]
reference = transaction_row.get('ref', 'N/A')
partner_name = transaction_row.get('partner_name', 'Non spécifié')

content = f"""Début du traitement de la transaction {transaction_number} sur {total_transactions}

📊 Détails de la transaction:
- Référence interne: #{move_id}
- Date: {date}
- Montant: {amount} {currency}
- Référence de paiement: {reference}
- Tiers: {partner_name}

Le système va maintenant analyser cette transaction pour déterminer comment la rapprocher."""

logger_message = self.audit_agent_loggeur(content=content, step_process='transaction_processing_start')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

### 2.2 Après Classification du Type de Transaction

**Emplacement**: Dans `BANKER_EXECUTION()`, après que l'IA ait identifié le type

**Log à ajouter**:
```python
# Après identification du type par l'IA
transaction_types = {
    'supplier_invoice': 'Paiement de facture fournisseur',
    'customer_invoice': 'Encaissement client',
    'direct_expense': 'Dépense directe sans facture préalable',
    'bank_transfer': 'Virement inter-bancaire',
    'other': 'Autre type de transaction'
}

transaction_type = identified_type  # Du résultat de l'IA
type_friendly = transaction_types.get(transaction_type, transaction_type)

content = f"""Type de transaction identifié: {type_friendly}

{'Le système va maintenant rechercher les factures fournisseurs ouvertes correspondantes.' if transaction_type == 'supplier_invoice' else ''}
{'Le système va maintenant rechercher les factures clients en attente de paiement.' if transaction_type == 'customer_invoice' else ''}
{'Le système va créer une écriture comptable directe sur un compte de charge.' if transaction_type == 'direct_expense' else ''}
{'Le système va traiter ce virement entre comptes bancaires.' if transaction_type == 'bank_transfer' else ''}"""

logger_message = self.audit_agent_loggeur(content=content, step_process='transaction_type_identified')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

### 2.3 Après Recherche de Facture (Succès)

**Emplacement**: Dans les méthodes `OPEN_AP_INVOICE_CHECK()` ou `OPEN_AR_INVOICE_CHECK()`

**Log à ajouter**:
```python
# Après recherche réussie de facture
invoice_found = matched_invoice
invoice_number = invoice_found.get('name', 'N/A')
invoice_amount = invoice_found.get('amount_total', 0)
invoice_residual = invoice_found.get('amount_residual', 0)
supplier_name = invoice_found.get('partner_name', 'N/A')

content = f"""Facture correspondante trouvée:

📄 Facture: {invoice_number}
🏢 Fournisseur: {supplier_name}
💰 Montant total: {invoice_amount} {currency}
💵 Montant restant à payer: {invoice_residual} {currency}

Vérification de la concordance avec le paiement de {amount} {currency}..."""

logger_message = self.audit_agent_loggeur(content=content, step_process='invoice_match_found')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

### 2.4 Après Validation des Montants

**Emplacement**: Après `check_invoice_balance()` dans les méthodes de check

**Logs à ajouter selon le cas**:

**a) Correspondance exacte:**
```python
content = f"""Validation des montants: ✅ Correspondance exacte

Le montant du paiement ({amount} {currency}) correspond exactement au montant restant de la facture.

Prochaine étape: Exécution du rapprochement comptable automatique."""

logger_message = self.audit_agent_loggeur(content=content, step_process='amount_validation_exact_match')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

**b) Paiement partiel:**
```python
content = f"""Validation des montants: ⚠️ Paiement partiel détecté

Montant du paiement: {amount} {currency}
Montant restant de la facture: {invoice_residual} {currency}
Différence: {abs(amount - invoice_residual)} {currency}

Le système va procéder à un rapprochement partiel. La facture restera partiellement ouverte pour le solde."""

logger_message = self.audit_agent_loggeur(content=content, step_process='amount_validation_partial_match')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

**c) Différence de change:**
```python
content = f"""Validation des montants: 💱 Différence de devise détectée

Paiement: {amount} {payment_currency}
Facture: {invoice_amount} {invoice_currency}

Le système va calculer automatiquement la différence de change et l'imputer sur le compte approprié:
- Gains de change: Compte {fx_profit_account_name}
- Pertes de change: Compte {fx_loss_account_name}"""

logger_message = self.audit_agent_loggeur(content=content, step_process='currency_difference_detected')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

### 2.5 Après Rapprochement Réussi (Amélioration)

**Emplacement**: Méthode `_TOOL_RECONCILE_PAIEMENT()`, à la fin (améliorer log existant ligne ~4252)

**Log actuel**: Log basique existant

**Log enrichi à remplacer**:
```python
# Après rapprochement réussi
content = f"""✅ Rapprochement comptable terminé avec succès!

Transaction #{move_id} rapprochée avec la facture {invoice_number}.

📝 Résumé du rapprochement:
- Type: {'Rapprochement complet' if is_full_reconcile else 'Rapprochement partiel'}
- Montant rapproché: {reconciled_amount} {currency}
{'- Solde restant: ' + str(remaining_balance) + ' ' + currency if not is_full_reconcile else ''}
- Date de comptabilisation: {accounting_date}

La transaction est maintenant marquée comme traitée dans le système."""

logger_message = self.audit_agent_loggeur(content=content, step_process='reconciliation_success')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

### 2.6 Pour les Dépenses Directes (sans facture)

**Emplacement**: Après décision de créer une écriture directe

**Log à ajouter**:
```python
# Pour dépense directe sans facture
selected_account = account_info
account_code = selected_account.get('code', 'N/A')
account_name = selected_account.get('name', 'N/A')

content = f"""Pas de facture préalable trouvée - Création d'une écriture comptable directe.

💳 Imputation comptable:
- Compte: {account_code} - {account_name}
- Montant: {amount} {currency}
- Libellé: {transaction_label}

Le système va maintenant générer l'écriture comptable dans le journal bancaire."""

logger_message = self.audit_agent_loggeur(content=content, step_process='direct_gl_entry_preparation')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

### 2.7 Mise en Attente (PENDING)

**Emplacement**: Quand une transaction est mise en attente

**Log à ajouter**:
```python
# Transaction mise en attente
content = f"""⏸️ Transaction mise en attente pour clarification

Référence: #{move_id}
Montant: {amount} {currency}

Raison de la suspension:
{pending_reason}

La transaction sera reprise ultérieurement avec les informations complémentaires. Le contexte de l'analyse a été sauvegardé."""

logger_message = self.audit_agent_loggeur(content=content, step_process='transaction_pending')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

### 2.8 Transaction Sautée (SKIPPED)

**Emplacement**: Quand une transaction est sautée

**Log à ajouter**:
```python
# Transaction sautée
content = f"""⏭️ Transaction sautée temporairement

Référence: #{move_id}
Montant: {amount} {currency}

Raison:
{skip_reason}

Cette transaction pourra être traitée manuellement plus tard ou lors d'une prochaine session."""

logger_message = self.audit_agent_loggeur(content=content, step_process='transaction_skipped')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

### 2.9 Fin du Traitement d'un Compte Bancaire

**Emplacement**: À la fin de `process_transactions()` pour un compte

**Log à ajouter**:
```python
# Résumé de fin pour le compte
content = f"""📊 Traitement du compte "{journal_name}" terminé

Statistiques:
- Total de transactions traitées: {processed_count}
- Rapprochements réussis: {success_count}
- Transactions en attente: {pending_count}
- Transactions sautées: {skipped_count}
- Erreurs: {error_count}

{'Passage au compte bancaire suivant...' if has_more_accounts else 'Tous les comptes ont été traités.'}"""

logger_message = self.audit_agent_loggeur(content=content, step_process='account_processing_complete')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

### 2.10 Demande d'Approbation Utilisateur

**Emplacement**: Quand `banker_approval_required` est True

**Log à ajouter**:
```python
# Avant demande d'approbation
content = f"""🔔 Validation requise de l'utilisateur

Le système a identifié un rapprochement possible mais nécessite votre validation:

Transaction: {amount} {currency} du {date}
Facture proposée: {invoice_number} ({invoice_amount} {currency})
Différence: {difference if any} {currency}

Veuillez confirmer ou refuser ce rapprochement."""

logger_message = self.audit_agent_loggeur(content=content, step_process='approval_request')
self.gchat_service.send_message_log(self.collection_name, self.sp_k, text=logger_message)
```

---

## 3. Principes Généraux pour Tous les Logs

### 3.1 Structure des Logs Métier

Un bon log métier doit contenir:
1. **Titre clair**: Ce qui se passe (ex: "Début du traitement...", "Classification terminée...")
2. **Détails pertinents**: Informations métier essentielles (montants, noms, dates)
3. **Contexte**: Pourquoi cette action (justification, raison)
4. **Prochaine étape**: Ce qui va suivre (optionnel mais utile)

### 3.2 Format Recommandé

```python
content = f"""[TITRE DE L'ÉTAPE]

[Section détails si nécessaire]
- Point 1: valeur1
- Point 2: valeur2

[Explication contextuelle]

[Prochaine étape si pertinent]"""

logger_message = self.audit_agent_loggeur(content=content, step_process='step_name')
self.space_manager.send_message_log(collection_name, thread_key=job_id, text=logger_message)
```

### 3.3 Ce qu'il Faut ÉVITER

❌ **Détails techniques**:
```python
# MAUVAIS
"Appel de la méthode process_tool_use() avec ModelSize.MEDIUM"
"Exécution de la fonction handle_expenses()"
```

✅ **Informations métier**:
```python
# BON
"Analyse du document pour identifier sa nature et son contenu"
"Traitement du document dans la catégorie Notes de frais"
```

❌ **Variables et noms techniques**:
```python
# MAUVAIS
"self.antho_router_manager a retourné service='INVOICES'"
"df_iterator.get_current_row() move_id=12345"
```

✅ **Informations compréhensibles**:
```python
# BON
"Le document a été classé dans la catégorie Factures"
"Traitement de la transaction numéro 12345"
```

### 3.4 Utilisation des Émojis (Optionnel mais Utile)

Pour améliorer la lisibilité, vous pouvez utiliser des émojis avec parcimonie:
- ✅ Succès
- ⚠️ Attention / Avertissement
- ❌ Erreur
- 📊 Statistiques / Résumé
- 📄 Document / Facture
- 💰 Montant / Finance
- 🏢 Entreprise / Fournisseur
- 📁 Dossier / Classement
- 🔔 Notification / Alerte
- ⏸️ Pause / Attente
- ⏭️ Saut
- 💱 Devise / Change

---

## 4. Plan d'Implémentation

### Phase 1: Router (new_router.py)
1. Ajouter log au début du traitement (section 1.1)
2. Ajouter log après extraction (section 1.2)
3. Ajouter log après résumé (section 1.3)
4. Ajouter log après année fiscale (section 1.4)
5. Améliorer log sélection département (section 1.5)
6. Enrichir logs workflow Drive (section 1.6)
7. Améliorer logs d'erreur (section 1.7)

### Phase 2: Banker (pybank.py)
1. Améliorer log début transaction (section 2.1)
2. Ajouter log classification type (section 2.2)
3. Ajouter log recherche facture (section 2.3)
4. Ajouter logs validation montants (section 2.4)
5. Améliorer log rapprochement réussi (section 2.5)
6. Ajouter log dépenses directes (section 2.6)
7. Ajouter logs PENDING/SKIPPED (sections 2.7-2.8)
8. Ajouter log fin de compte (section 2.9)
9. Ajouter log approbation (section 2.10)

### Phase 3: Test et Ajustement
1. Tester avec des documents réels
2. Vérifier que l'agent principal comprend bien les logs
3. Ajuster le niveau de détail selon les retours
4. S'assurer que les logs sont clairs en français ET en anglais si nécessaire

---

## 5. Validation

Pour valider que les logs sont bien enrichis:

1. **Tester l'agent principal**: Poser des questions comme:
   - "Où en est le traitement du document X?"
   - "Pourquoi ce document a été classé dans ce département?"
   - "Qu'est-ce qui s'est passé avec la transaction Y?"

2. **L'agent doit pouvoir répondre** avec précision en se basant sur les logs

3. **Indicateurs de succès**:
   - L'agent peut expliquer chaque étape du processus
   - L'agent peut justifier les décisions prises
   - L'agent peut donner le statut actuel sans ambiguïté
   - L'utilisateur comprend ce qui se passe sans avoir besoin de détails techniques

---

**Date de création**: 5 novembre 2025
**Version**: 1.0
**Auteur**: Assistant Pinnokio
