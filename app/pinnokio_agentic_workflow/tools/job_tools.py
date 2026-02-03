"""
JobTools - Outils de recherche et filtrage des jobs par département
3 outils distincts : APBookkeeper, Router, Bank + Context Tools
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import pandas as pd

logger = logging.getLogger("pinnokio.job_tools")


class APBookkeeperJobTools:
    """
    Outil GET_APBOOKEEPER_JOBS pour rechercher les factures fournisseur.
    
    Output enrichi avec drive_file_id pour permettre à l'agent de voir les documents.
    
    ⭐ NOUVEAU : Recharge depuis Redis à chaque appel (mode UI) pour données à jour
    """
    
    def __init__(self, jobs_data: Dict, user_id: str = None, company_id: str = None, user_context: Dict = None, mode: str = "UI"):
        self.ap_data = jobs_data.get("APBOOKEEPER", {})  # Données initiales (fallback)
        self.user_id = user_id
        self.company_id = company_id
        self.user_context = user_context or {}
        self.mode = mode
        logger.info(f"[APBOOKEEPER_TOOLS] Initialisé avec {len(self.ap_data.get('to_do', []))} factures to_do (mode={mode})")
    
    def get_tool_definition(self) -> Dict:
        """Définition COURTE de l'outil GET_APBOOKEEPER_JOBS (pour l'API)."""
        return {
            "name": "GET_APBOOKEEPER_JOBS",
            "description": "📋 Recherche les factures fournisseur par statut/nom. Retourne job_id, drive_file_id, file_name, status. Utilisez GET_TOOL_HELP pour plus de détails.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["to_do", "in_process", "pending", "processed", "all"],
                        "description": "Filtrer par statut (défaut: to_do)"
                    },
                    "file_name_contains": {
                        "type": "string",
                        "description": "Rechercher dans le nom du fichier (case insensitive)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de résultats (défaut: 50, max: 200)",
                        "default": 50
                    }
                },
                "required": []
            }
        }
    
    async def search(
        self,
        status: str = "to_do",
        file_name_contains: str = None,
        limit: int = 50
    ) -> Dict:
        """
        Recherche les factures APBookkeeper.
        
        ⭐ NOUVEAU : Recharge depuis Redis à chaque appel (mode UI) pour données à jour
        """
        try:
            logger.info(f"[GET_APBOOKEEPER_JOBS] Recherche - status={status}, file_name={file_name_contains}, limit={limit}")
            
            # ⭐ Recharger depuis Redis si mode UI (données à jour)
            ap_data = self.ap_data  # Fallback vers données initiales
            if self.mode == "UI" and self.user_id and self.company_id:
                try:
                    from ..tools.job_loader import JobLoader
                    loader = JobLoader(
                        user_id=self.user_id,
                        company_id=self.company_id,
                        client_uuid=self.user_context.get("client_uuid")
                    )
                    # Recharger uniquement APBookkeeper depuis Redis
                    fresh_ap_data = await loader.load_apbookeeper_jobs(mode="UI")
                    if fresh_ap_data:
                        ap_data = fresh_ap_data
                        logger.info(f"[GET_APBOOKEEPER_JOBS] ✅ Données rechargées depuis Redis - {len(ap_data.get('to_do', []))} factures to_do")
                except Exception as e:
                    logger.warning(f"[GET_APBOOKEEPER_JOBS] ⚠️ Erreur rechargement Redis: {e} - Utilisation données initiales")
            
            limit = min(limit, 200)
            
            # Récupérer les jobs selon le statut
            if status == "all":
                all_jobs = []
                all_jobs.extend(ap_data.get("to_do", []))  # ✅ Utiliser données rechargées
                all_jobs.extend(ap_data.get("in_process", []))
                all_jobs.extend(ap_data.get("pending", []))
                all_jobs.extend(ap_data.get("processed", []))
            else:
                status_key = "processed" if status == "completed" else status
                all_jobs = ap_data.get(status_key, [])
            
            # Filtrer par nom de fichier
            filtered_jobs = all_jobs
            if file_name_contains:
                filtered_jobs = [
                    job for job in filtered_jobs
                    if file_name_contains.lower() in job.get("file_name", "").lower()
                ]
            
            # Limiter les résultats
            filtered_jobs = filtered_jobs[:limit]
            
            # Output enrichi avec drive_file_id pour visualisation
            results = []
            for job in filtered_jobs:
                results.append({
                    "job_id": job.get("job_id"),
                    "drive_file_id": job.get("drive_file_id"),  # 🔍 Pour voir le document
                    "uri_drive_link": job.get("uri_drive_link"),  # Lien direct
                    "file_name": job.get("file_name"),
                    "status": job.get("status"),
                    "timestamp": job.get("timestamp"),
                    "source": job.get("source", ""),
                    "client": job.get("client", "")
                })
            
            return {
                "success": True,
                "department": "APBOOKEEPER",
                "filters_applied": {
                    "status": status,
                    "file_name_contains": file_name_contains
                },
                "total_found": len(results),
                "results": results,
                "summary": f"📋 {len(results)} facture(s) fournisseur (statut: {status})"
            }
        
        except Exception as e:
            logger.error(f"[GET_APBOOKEEPER_JOBS] Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "results": []
            }


class RouterJobTools:
    """
    Outil GET_ROUTER_JOBS pour rechercher les documents à router.
    
    Output enrichi avec drive_file_id et router_drive_view_link pour visualisation.
    
    ⭐ NOUVEAU : Recharge depuis Redis à chaque appel (mode UI) pour données à jour
    """
    
    def __init__(self, jobs_data: Dict, user_id: str = None, company_id: str = None, user_context: Dict = None, mode: str = "UI"):
        self.router_data = jobs_data.get("ROUTER", {})  # Données initiales (fallback)
        self.user_id = user_id
        self.company_id = company_id
        self.user_context = user_context or {}
        self.mode = mode
        logger.info(f"[ROUTER_TOOLS] Initialisé avec {len(self.router_data.get('to_process', []))} documents to_process (mode={mode})")
    
    def get_tool_definition(self) -> Dict:
        """Définition COURTE de l'outil GET_ROUTER_JOBS (pour l'API)."""
        return {
            "name": "GET_ROUTER_JOBS",
            "description": "🗂️ Recherche les documents à router par statut/nom. Retourne drive_file_id, file_name, status. Utilisez GET_TOOL_HELP pour plus de détails.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["to_process", "in_process", "all"],
                        "description": "Filtrer par statut (défaut: to_process)"
                    },
                    "file_name_contains": {
                        "type": "string",
                        "description": "Rechercher dans le nom du fichier (case insensitive)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de résultats (défaut: 50, max: 200)",
                        "default": 50
                    }
                },
                "required": []
            }
        }
    
    async def search(
        self,
        status: str = "to_process",
        file_name_contains: str = None,
        limit: int = 50
    ) -> Dict:
        """
        Recherche les documents Router.
        
        ⭐ NOUVEAU : Recharge depuis Redis à chaque appel (mode UI) pour données à jour
        """
        try:
            logger.info(f"[GET_ROUTER_JOBS] Recherche - status={status}, file_name={file_name_contains}, limit={limit}")
            
            # ⭐ Recharger depuis Redis si mode UI (données à jour)
            router_data = self.router_data  # Fallback vers données initiales
            if self.mode == "UI" and self.user_id and self.company_id:
                try:
                    from ..tools.job_loader import JobLoader
                    loader = JobLoader(
                        user_id=self.user_id,
                        company_id=self.company_id,
                        client_uuid=self.user_context.get("client_uuid")
                    )
                    # Recharger uniquement Router depuis Redis
                    fresh_router_data = await loader.load_router_jobs(mode="UI", user_context=self.user_context)
                    if fresh_router_data:
                        router_data = fresh_router_data
                        logger.info(f"[GET_ROUTER_JOBS] ✅ Données rechargées depuis Redis - {len(router_data.get('to_process', []))} documents to_process")
                except Exception as e:
                    logger.warning(f"[GET_ROUTER_JOBS] ⚠️ Erreur rechargement Redis: {e} - Utilisation données initiales")
            
            limit = min(limit, 200)
            
            # Mapping statut pour format Reflex
            status_mapping = {
                "to_process": "to_process",  # ✅ Corrigé : doit correspondre à job_loader
                "in_process": "in_process",
                "processed": "processed"
            }
            
            # Récupérer les jobs selon le statut (format Reflex)
            if status == "all":
                all_jobs = []
                all_jobs.extend(router_data.get("to_process", []))  # ✅ Utiliser données rechargées
                all_jobs.extend(router_data.get("in_process", []))
                all_jobs.extend(router_data.get("processed", []))
            else:
                reflex_status = status_mapping.get(status, status)
                all_jobs = router_data.get(reflex_status, [])
            
            # Filtrer par nom de fichier (format Reflex utilise "name")
            filtered_jobs = all_jobs
            if file_name_contains:
                filtered_jobs = [
                    job for job in filtered_jobs
                    if file_name_contains.lower() in job.get("name", "").lower()
                ]
            
            # Limiter les résultats
            filtered_jobs = filtered_jobs[:limit]
            
            # Output enrichi avec drive_file_id pour visualisation
            results = []
            for job in filtered_jobs:
                results.append({
                    "drive_file_id": job.get("id"),  # 🔍 Pour voir le document (format Reflex)
                    "router_drive_view_link": job.get("router_drive_view_link"),  # Lien direct
                    "file_name": job.get("name"),  # Format Reflex utilise "name"
                    "status": job.get("status"),
                    "created_time": job.get("created_time")
                })
            
            return {
                "success": True,
                "department": "ROUTER",
                "filters_applied": {
                    "status": status,
                    "file_name_contains": file_name_contains
                },
                "total_found": len(results),
                "results": results,
                "summary": f"🗂️ {len(results)} document(s) à router (statut: {status})"
            }
        
        except Exception as e:
            logger.error(f"[GET_ROUTER_JOBS] Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "results": []
            }


class BankJobTools:
    """
    Outil GET_BANK_TRANSACTIONS pour rechercher les transactions bancaires.
    
    Output complet avec tous les détails des transactions pour analyse approfondie.
    
    ⭐ NOUVEAU : Recharge depuis Redis à chaque appel (mode UI) pour données à jour
    """
    
    def __init__(self, jobs_data: Dict, user_id: str = None, company_id: str = None, user_context: Dict = None, mode: str = "UI"):
        self.bank_data = jobs_data.get("BANK", {})  # Données initiales (fallback)
        self.user_id = user_id
        self.company_id = company_id
        self.user_context = user_context or {}
        self.mode = mode
        logger.info(f"[BANK_TOOLS] Initialisé avec {len(self.bank_data.get('to_reconcile', []))} transactions to_reconcile (mode={mode})")
    
    def get_tool_definition(self) -> Dict:
        """Définition COURTE de l'outil GET_BANK_TRANSACTIONS (pour l'API)."""
        return {
            "name": "GET_BANK_TRANSACTIONS",
            "description": "🏦 Recherche les transactions bancaires par statut/compte/montant/date. Retourne transaction_id, journal_id, amount, date, partner. Utilisez GET_TOOL_HELP pour plus de détails.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["to_reconcile", "in_process", "pending", "all"],
                        "description": "Filtrer par statut (défaut: to_reconcile)"
                    },
                    "journal_id": {
                        "type": "string",
                        "description": "Filtrer par compte bancaire (journal_id)"
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Date de début (YYYY-MM-DD)"
                    },
                    "date_to": {
                        "type": "string",
                        "description": "Date de fin (YYYY-MM-DD)"
                    },
                    "amount_min": {
                        "type": "number",
                        "description": "Montant minimum (€)"
                    },
                    "amount_max": {
                        "type": "number",
                        "description": "Montant maximum (€)"
                    },
                    "partner_name_contains": {
                        "type": "string",
                        "description": "Rechercher dans le nom du partenaire (case insensitive)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de résultats (défaut: 50, max: 200)",
                        "default": 50
                    }
                },
                "required": []
            }
        }
    
    async def search(
        self,
        status: str = "to_reconcile",
        journal_id: str = None,
        date_from: str = None,
        date_to: str = None,
        amount_min: float = None,
        amount_max: float = None,
        partner_name_contains: str = None,
        limit: int = 50
    ) -> Dict:
        """
        Recherche les transactions bancaires.
        
        ⭐ NOUVEAU : Recharge depuis Redis à chaque appel (mode UI) pour données à jour
        """
        try:
            logger.info(f"[GET_BANK_TRANSACTIONS] Recherche - status={status}, journal={journal_id}, limit={limit}")
            
            # ⭐ Recharger depuis Redis si mode UI (données à jour)
            bank_data = self.bank_data  # Fallback vers données initiales
            if self.mode == "UI" and self.user_id and self.company_id:
                try:
                    from ..tools.job_loader import JobLoader
                    loader = JobLoader(
                        user_id=self.user_id,
                        company_id=self.company_id,
                        client_uuid=self.user_context.get("client_uuid")
                    )
                    # Recharger uniquement Bank depuis Redis
                    fresh_bank_data = await loader.load_bank_transactions(mode="UI", user_context=self.user_context)
                    if fresh_bank_data:
                        bank_data = fresh_bank_data
                        logger.info(f"[GET_BANK_TRANSACTIONS] ✅ Données rechargées depuis Redis - {len(bank_data.get('to_reconcile', []))} transactions to_reconcile")
                except Exception as e:
                    logger.warning(f"[GET_BANK_TRANSACTIONS] ⚠️ Erreur rechargement Redis: {e} - Utilisation données initiales")
            
            limit = min(limit, 200)
            
            # Récupérer les transactions selon le statut
            if status == "all":
                all_txs = []
                all_txs.extend(bank_data.get("to_reconcile", []))  # ✅ Utiliser données rechargées
                all_txs.extend(bank_data.get("pending", []))
                all_txs.extend(bank_data.get("in_process", []))
            else:
                all_txs = bank_data.get(status, [])
            
            if not all_txs:
                return {
                    "success": True,
                    "department": "BANK",
                    "filters_applied": {},
                    "total_found": 0,
                    "total_amount": 0,
                    "results": [],
                    "summary": "🏦 Aucune transaction bancaire trouvée"
                }
            
            # Convertir en DataFrame pour filtrage avancé
            df = pd.DataFrame(all_txs)
            
            # Appliquer les filtres
            if journal_id:
                df = df[df['journal_id'].astype(str).str.contains(journal_id, case=False, na=False)]
            
            if date_from:
                df = df[df['date'] >= date_from]
            
            if date_to:
                df = df[df['date'] <= date_to]
            
            if amount_min is not None:
                df = df[df['amount'] >= amount_min]
            
            if amount_max is not None:
                df = df[df['amount'] <= amount_max]
            
            if partner_name_contains:
                df = df[df['partner_name'].astype(str).str.contains(partner_name_contains, case=False, na=False)]
            
            # Limiter les résultats
            df = df.head(limit)
            
            # Output complet avec tous les détails
            results = []
            for _, row in df.iterrows():
                results.append({
                    "transaction_id": row.get("transaction_id"),  # ID pour payload LPT
                    "journal_id": row.get("journal_id"),  # Compte bancaire
                    "date": str(row.get("date", "")),
                    "amount": float(row.get("amount", 0)),
                    "partner_name": str(row.get("partner_name", "")),
                    "partner_id": row.get("partner_id"),
                    "payment_ref": str(row.get("payment_ref", "")),
                    "ref": str(row.get("ref", "")),
                    "transaction_type": str(row.get("transaction_type", "")),
                    "currency_id": str(row.get("currency_id", "")),
                    "amount_residual": float(row.get("amount_residual", 0)),
                    "is_reconciled": bool(row.get("is_reconciled", False)),
                    "display_name": str(row.get("display_name", "")),
                    "state": str(row.get("state", "")),
                    "status": str(row.get("status", "to_reconcile"))
                })
            
            # Calculer le total
            total_amount = sum(r["amount"] for r in results)
            
            # Résumé
            journal_info = f" sur {journal_id}" if journal_id else ""
            partner_info = f" pour {partner_name_contains}" if partner_name_contains else ""
            
            return {
                "success": True,
                "department": "BANK",
                "filters_applied": {
                    "status": status,
                    "journal_id": journal_id,
                    "date_from": date_from,
                    "date_to": date_to,
                    "amount_min": amount_min,
                    "amount_max": amount_max,
                    "partner_name_contains": partner_name_contains
                },
                "total_found": len(results),
                "total_amount": round(total_amount, 2),
                "results": results,
                "summary": f"🏦 {len(results)} transaction(s){journal_info}{partner_info} - Total: {total_amount:.2f}€"
            }
        
        except Exception as e:
            logger.error(f"[GET_BANK_TRANSACTIONS] Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "results": []
            }


class ExpenseJobTools:
    """
    Outil GET_EXPENSES_INFO pour rechercher les notes de frais.
    
    Output complet avec tous les détails des expenses pour analyse approfondie.
    Inclut drive_file_id pour visualisation des documents.
    
    ⭐ NOUVEAU : Recharge depuis Redis à chaque appel (mode UI) pour données à jour
    """
    
    def __init__(self, jobs_data: Dict, user_id: str = None, company_id: str = None, user_context: Dict = None, mode: str = "UI"):
        self.expenses_data = jobs_data.get("EXPENSES", {})  # Données initiales (fallback)
        self.user_id = user_id
        self.company_id = company_id
        self.user_context = user_context or {}
        self.mode = mode
        logger.info(f"[EXPENSES_TOOLS] Initialisé avec {len(self.expenses_data.get('open', []))} expenses open (mode={mode})")
    
    def get_tool_definition(self) -> Dict:
        """Définition COURTE de l'outil GET_EXPENSES_INFO (pour l'API)."""
        return {
            "name": "GET_EXPENSES_INFO",
            "description": "💰 Recherche les notes de frais par statut/date/montant/fournisseur. Retourne expense_id, drive_file_id, date, amount, supplier, status. Utilisez GET_TOOL_HELP pour plus de détails.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["open", "closed", "all"],
                        "description": "Filtrer par statut (open=non saisies, closed=comptabilisées, défaut: open)"
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Date de début (YYYY-MM-DD)"
                    },
                    "date_to": {
                        "type": "string",
                        "description": "Date de fin (YYYY-MM-DD)"
                    },
                    "amount_min": {
                        "type": "number",
                        "description": "Montant minimum"
                    },
                    "amount_max": {
                        "type": "number",
                        "description": "Montant maximum"
                    },
                    "supplier_contains": {
                        "type": "string",
                        "description": "Rechercher dans le nom du fournisseur (case insensitive)"
                    },
                    "payment_method": {
                        "type": "string",
                        "description": "Filtrer par méthode de paiement"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de résultats (défaut: 50, max: 200)",
                        "default": 50
                    }
                },
                "required": []
            }
        }
    
    async def search(
        self,
        status: str = "open",
        date_from: str = None,
        date_to: str = None,
        amount_min: float = None,
        amount_max: float = None,
        supplier_contains: str = None,
        payment_method: str = None,
        limit: int = 50
    ) -> Dict:
        """
        Recherche les notes de frais.
        
        ⭐ NOUVEAU : Recharge depuis Redis à chaque appel (mode UI) pour données à jour
        """
        try:
            logger.info(f"[GET_EXPENSES_INFO] Recherche - status={status}, limit={limit}")
            
            # ⭐ Recharger depuis Redis si mode UI (données à jour)
            expenses_data = self.expenses_data  # Fallback vers données initiales
            if self.mode == "UI" and self.user_id and self.company_id:
                try:
                    from ..tools.job_loader import JobLoader
                    loader = JobLoader(
                        user_id=self.user_id,
                        company_id=self.company_id,
                        client_uuid=self.user_context.get("client_uuid")
                    )
                    # Recharger uniquement Expenses depuis Redis
                    fresh_expenses_data = await loader.load_expenses(mode="UI", user_context=self.user_context)
                    if fresh_expenses_data:
                        expenses_data = fresh_expenses_data
                        logger.info(f"[GET_EXPENSES_INFO] ✅ Données rechargées depuis Redis - {len(expenses_data.get('open', []))} open, {len(expenses_data.get('closed', []))} closed")
                except Exception as e:
                    logger.warning(f"[GET_EXPENSES_INFO] ⚠️ Erreur rechargement Redis: {e} - Utilisation données initiales")
            
            limit = min(limit, 200)
            
            # Récupérer les expenses selon le statut
            if status == "all":
                all_expenses = []
                all_expenses.extend(expenses_data.get("open", []))  # ✅ Utiliser données rechargées
                all_expenses.extend(expenses_data.get("closed", []))
            elif status == "closed":
                all_expenses = expenses_data.get("closed", [])
            else:  # "open" par défaut
                all_expenses = expenses_data.get("open", [])
            
            if not all_expenses:
                return {
                    "success": True,
                    "department": "EXPENSES",
                    "filters_applied": {},
                    "total_found": 0,
                    "total_amount": 0,
                    "results": [],
                    "summary": "💰 Aucune note de frais trouvée"
                }
            
            # Convertir en DataFrame pour filtrage avancé
            df = pd.DataFrame(all_expenses)
            
            # Appliquer les filtres
            if date_from:
                df = df[df['date'] >= date_from]
            
            if date_to:
                df = df[df['date'] <= date_to]
            
            if amount_min is not None:
                df = df[df['amount'] >= amount_min]
            
            if amount_max is not None:
                df = df[df['amount'] <= amount_max]
            
            if supplier_contains:
                df = df[df['supplier'].astype(str).str.contains(supplier_contains, case=False, na=False)]
            
            if payment_method:
                df = df[df['payment_method'].astype(str).str.contains(payment_method, case=False, na=False)]
            
            # Limiter les résultats
            df = df.head(limit)
            
            # Output complet avec tous les détails
            results = []
            for _, row in df.iterrows():
                results.append({
                    "expense_id": row.get("expense_id"),  # ID pour référence
                    "drive_file_id": row.get("drive_file_id"),  # 🔍 Pour voir le document
                    "date": str(row.get("date", "")),
                    "amount": float(row.get("amount", 0)),
                    "currency": str(row.get("currency", "CHF")),
                    "supplier": str(row.get("supplier", "")),
                    "status": str(row.get("status", "to_process")),  # "to_process" ou "close"
                    "concern": str(row.get("concern", "")),
                    "payment_method": str(row.get("payment_method", "")),
                    "job_id": row.get("job_id", ""),
                    "file_name": row.get("file_name", "")
                })
            
            # Calculer le total
            total_amount = sum(r["amount"] for r in results)
            
            # Résumé
            status_info = f" ({status})" if status != "all" else ""
            supplier_info = f" pour {supplier_contains}" if supplier_contains else ""
            
            return {
                "success": True,
                "department": "EXPENSES",
                "filters_applied": {
                    "status": status,
                    "date_from": date_from,
                    "date_to": date_to,
                    "amount_min": amount_min,
                    "amount_max": amount_max,
                    "supplier_contains": supplier_contains,
                    "payment_method": payment_method
                },
                "total_found": len(results),
                "total_amount": round(total_amount, 2),
                "results": results,
                "summary": f"💰 {len(results)} note(s) de frais{status_info}{supplier_info} - Total: {total_amount:.2f}{results[0]['currency'] if results else 'CHF'}"
            }
        
        except Exception as e:
            logger.error(f"[GET_EXPENSES_INFO] Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "results": []
            }


class ContextTools:
    """
    Outils d'accès et de modification des contextes (Router, APBookkeeper, Company).
    
    Ces outils permettent à l'agent principal d'accéder directement aux contextes métier
    sans passer par un SPT agent.
    """
    
    def __init__(self, firebase_management, firebase_user_id: str, collection_name: str, brain=None):
        """
        Args:
            firebase_management: Instance FirebaseManagement pour accès aux données
            firebase_user_id: ID utilisateur Firebase
            collection_name: Nom de la collection (ex: klk_space_id_8b2dce)
            brain: Instance PinnokioBrain pour accès au user_context
        """
        self.firebase_management = firebase_management
        self.firebase_user_id = firebase_user_id
        self.collection_name = collection_name
        self.brain = brain
        
        # Récupérer le mandate_path depuis le brain (déjà chargé)
        if brain:
            user_context = brain.get_user_context()
            self.mandate_path = user_context.get("mandate_path")
        else:
            logger.warning("[CONTEXT_TOOLS] Brain non fourni, mandate_path sera None")
            self.mandate_path = None
        
        if not self.mandate_path:
            logger.warning("[CONTEXT_TOOLS] mandate_path non trouvé dans user_context")
        
        # Initialiser le TextUpdaterAgent pour UPDATE_CONTEXT
        self.text_updater = None
        
        # Stocker les propositions de mise à jour (avant publication)
        self.pending_proposal = None
        
        logger.info(f"[CONTEXT_TOOLS] Initialisé avec mandate_path={self.mandate_path}")
    
    def _init_text_updater(self):
        """Initialise le TextUpdaterAgent (lazy loading)."""
        if self.text_updater is None:
            from .text_updater import TextUpdaterAgent
            self.text_updater = TextUpdaterAgent(
                collection_name=self.collection_name,
                firebase_user_id=self.firebase_user_id
            )
    
    # ═══════════════════════════════════════════════════════════════
    # OUTILS DE LECTURE DES CONTEXTES
    # ═══════════════════════════════════════════════════════════════
    
    def get_router_prompt_definition(self) -> Dict:
        """Définition COURTE de l'outil ROUTER_PROMPT (pour l'API)."""
        return {
            "name": "ROUTER_PROMPT",
            "description": "🗂️ Règles de classification par service (hr, invoices, expenses, banks_cash, taxes, contrats, letters, financial_statement). Utilisez GET_TOOL_HELP pour plus de détails.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": "Nom du service (ex: hr, banks_cash, taxes, legal, etc.)"
                    }
                },
                "required": ["service"]
            }
        }
    
    async def get_router_prompt(self, service: str) -> Dict:
        """
        Récupère le prompt de routage pour un service spécifique.
        
        Args:
            service: Nom du service (ex: "hr", "banks_cash", etc.)
        
        Returns:
            Dict avec le prompt de routage
        """
        try:
            logger.info(f"[ROUTER_PROMPT] Recherche pour service={service}")
            
            if not self.mandate_path:
                return {
                    "success": False,
                    "error": "mandate_path non configuré"
                }
            
            # Récupérer tous les contextes
            all_contexts = self.firebase_management.get_all_contexts(self.mandate_path)
            
            if not all_contexts or "router" not in all_contexts:
                return {
                    "success": False,
                    "error": "router_context non trouvé dans Firebase"
                }
            
            router_context = all_contexts["router"]
            router_prompt_data = router_context.get("router_prompt", {})
            
            # Extraire le prompt pour le service demandé
            service_prompt = router_prompt_data.get(service)
            
            if not service_prompt:
                available_services = list(router_prompt_data.keys())
                return {
                    "success": False,
                    "error": f"Service '{service}' non trouvé",
                    "available_services": available_services,
                    "hint": f"Services disponibles: {', '.join(available_services)}"
                }
            
            return {
                "success": True,
                "service": service,
                "routing_rules": service_prompt,
                "last_refresh": router_context.get("last_refresh"),
                "summary": f"📋 Règles de routage pour le service '{service}' récupérées avec succès"
            }
        
        except Exception as e:
            logger.error(f"[ROUTER_PROMPT] Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_apbookeeper_context_definition(self) -> Dict:
        """Définition COURTE de l'outil APBOOKEEPER_CONTEXT (pour l'API)."""
        return {
            "name": "APBOOKEEPER_CONTEXT",
            "description": "📊 Contexte comptable complet : règles comptables, TVA, plan comptable, workflows. Utilisez GET_TOOL_HELP pour plus de détails.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    
    async def get_apbookeeper_context(self) -> Dict:
        """
        Récupère le contexte comptable complet.
        
        Returns:
            Dict avec le contexte comptable
        """
        try:
            logger.info("[APBOOKEEPER_CONTEXT] Récupération du contexte comptable")
            
            if not self.mandate_path:
                return {
                    "success": False,
                    "error": "mandate_path non configuré"
                }
            
            # Récupérer tous les contextes
            all_contexts = self.firebase_management.get_all_contexts(self.mandate_path)
            
            if not all_contexts or "accounting" not in all_contexts:
                return {
                    "success": False,
                    "error": "accounting_context non trouvé dans Firebase"
                }
            
            accounting_context = all_contexts["accounting"]
            
            # Extraire accounting_context_0
            accounting_content = accounting_context.get("accounting_context_0", "")
            
            if not accounting_content:
                return {
                    "success": False,
                    "error": "accounting_context_0 est vide"
                }
            
            return {
                "success": True,
                "accounting_context": accounting_content,
                "last_refresh": accounting_context.get("last_refresh"),
                "content_length": len(str(accounting_content)),
                "summary": f"📊 Contexte comptable récupéré ({len(str(accounting_content))} caractères)"
            }
        
        except Exception as e:
            logger.error(f"[APBOOKEEPER_CONTEXT] Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    def get_bank_context_definition(self) -> Dict:
        """Définition COURTE de l'outil BANK_CONTEXT (pour l'API)."""
        return {
            "name": "BANK_CONTEXT",
            "description": "🏦 Contexte bancaire de l'entreprise (règles & conventions de rapprochement, libellés, tolérances, comptes, etc.). ⚠️ Ne pas confondre avec ROUTER_PROMPT (règles de routage). Utilisez GET_TOOL_HELP pour plus de détails.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }

    async def get_bank_context(self) -> Dict:
        """
        Récupère le contexte bancaire complet.
        
        Source Firebase:
            {mandate_path}/context/bank_context (champ data.bank_context_0)
        """
        try:
            logger.info("[BANK_CONTEXT] Récupération du contexte bancaire")
            
            if not self.mandate_path:
                return {
                    "success": False,
                    "error": "mandate_path non configuré"
                }
            
            all_contexts = self.firebase_management.get_all_contexts(self.mandate_path)
            
            if not all_contexts or "bank" not in all_contexts:
                return {
                    "success": False,
                    "error": "bank_context non trouvé dans Firebase"
                }
            
            bank_context = all_contexts["bank"]
            bank_content = bank_context.get("bank_context_0", "")
            
            if not bank_content:
                return {
                    "success": False,
                    "error": "bank_context_0 est vide"
                }
            
            return {
                "success": True,
                "bank_context": bank_content,
                "last_refresh": bank_context.get("last_refresh"),
                "content_length": len(str(bank_content)),
                "summary": f"🏦 Contexte bancaire récupéré ({len(str(bank_content))} caractères)"
            }
        
        except Exception as e:
            logger.error(f"[BANK_CONTEXT] Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_company_context_definition(self) -> Dict:
        """Définition COURTE de l'outil COMPANY_CONTEXT (pour l'API)."""
        return {
            "name": "COMPANY_CONTEXT",
            "description": "🏢 Profil complet de l'entreprise : informations légales, activité, structure. Utilisez GET_TOOL_HELP pour plus de détails.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    
    async def get_company_context(self) -> Dict:
        """
        Récupère le profil complet de l'entreprise.
        
        Returns:
            Dict avec le profil de l'entreprise
        """
        try:
            logger.info("[COMPANY_CONTEXT] Récupération du profil entreprise")
            
            if not self.mandate_path:
                return {
                    "success": False,
                    "error": "mandate_path non configuré"
                }
            
            # Récupérer tous les contextes
            all_contexts = self.firebase_management.get_all_contexts(self.mandate_path)
            
            if not all_contexts or "general" not in all_contexts:
                return {
                    "success": False,
                    "error": "general_context non trouvé dans Firebase"
                }
            
            general_context = all_contexts["general"]
            
            # Extraire context_company_profile_report
            company_profile = general_context.get("context_company_profile_report", "")
            
            if not company_profile:
                return {
                    "success": False,
                    "error": "context_company_profile_report est vide"
                }
            
            return {
                "success": True,
                "company_profile": company_profile,
                "last_refresh": general_context.get("last_refresh"),
                "content_length": len(str(company_profile)),
                "summary": f"🏢 Profil entreprise récupéré ({len(str(company_profile))} caractères)"
            }
        
        except Exception as e:
            logger.error(f"[COMPANY_CONTEXT] Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    # ═══════════════════════════════════════════════════════════════
    # OUTILS DE MODIFICATION DES CONTEXTES
    # ═══════════════════════════════════════════════════════════════
    
    async def _save_context_to_firebase(
        self,
        context_type: str,
        service_name: str,
        updated_text: str
        ) -> Dict:
        """
        Méthode interne pour sauvegarder un contexte dans Firebase.
        Extrait de PUBLISH_CONTEXT pour réutilisation dans UPDATE_CONTEXT.
        
        Args:
            context_type: Type de contexte (router/accounting/company)
            service_name: Nom du service (requis pour router)
            updated_text: Texte mis à jour à sauvegarder
        
        Returns:
            Dict avec success, context_path, last_refresh
        """
        try:
            if context_type == "router":
                if not service_name:
                    return {"success": False, "error": "service_name requis pour context_type='router'"}
                
                # Récupérer le router_prompt complet actuel
                all_contexts = self.firebase_management.get_all_contexts(self.mandate_path)
                router_context = all_contexts.get("router", {})
                router_prompt_data = router_context.get("router_prompt", {})
                
                # Mettre à jour uniquement le service modifié
                router_prompt_data[service_name] = updated_text
                
                # Sauvegarder avec update_router_context
                success = self.firebase_management.update_router_context(
                    mandate_path=self.mandate_path,
                    updated_content=router_prompt_data
                )
                
                context_path = f"{self.mandate_path}/context/router_context/router_prompt/{service_name}"
            
            elif context_type == "accounting":
                # update_accounting_context attend le texte directement
                success = self.firebase_management.update_accounting_context(
                    mandate_path=self.mandate_path,
                    updated_content=updated_text
                )
                
                context_path = f"{self.mandate_path}/context/accounting_context/data/accounting_context_0"

            elif context_type == "bank":
                success = self.firebase_management.update_bank_context(
                    mandate_path=self.mandate_path,
                    updated_content=updated_text
                )
                
                context_path = f"{self.mandate_path}/context/bank_context/data/bank_context_0"
            
            elif context_type == "company":
                success = self.firebase_management.update_general_context(
                    mandate_path=self.mandate_path,
                    updated_content=updated_text
                )
                
                context_path = f"{self.mandate_path}/context/general_context/context_company_profile_report"
            
            else:
                return {"success": False, "error": f"Type de contexte inconnu: {context_type}"}
            
            if not success:
                return {"success": False, "error": "Échec de la sauvegarde Firebase"}
            
            last_refresh = datetime.now(timezone.utc).isoformat()
            logger.info(
                f"[SAVE_FIREBASE] ✅ Sauvegarde réussie - "
                f"type={context_type}, path={context_path}"
            )
            
            return {
                "success": True,
                "context_path": context_path,
                "last_refresh": last_refresh
            }
        
        except Exception as e:
            logger.error(f"[SAVE_FIREBASE] ❌ Erreur sauvegarde: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def get_update_context_definition(self) -> Dict:
        """Définition COURTE de l'outil UPDATE_CONTEXT (pour l'API)."""
        return {
            "name": "UPDATE_CONTEXT",
            "description": "✏️ Mise à jour atomique d'un contexte (router/accounting/bank/company). Utilise des ANCRES (12+ caractères avant/après) pour localiser précisément la zone à modifier. Opérations: add, replace, delete. Utilisez GET_TOOL_HELP pour plus de détails.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "context_type": {
                        "type": "string",
                        "enum": ["router", "accounting", "bank", "company"],
                        "description": "Type de contexte à modifier"
                    },
                    "service_name": {
                        "type": "string",
                        "description": "Nom du service (requis si context_type=router, ex: hr, banks_cash, letters, etc.)"
                    },
                    "operations": {
                        "type": "array",
                        "description": "Liste des opérations avec ancres. 🎯 Utilisez anchor_before/anchor_after (12+ caractères) pour localiser la zone.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "operation": {
                                    "type": "string",
                                    "enum": ["add", "replace", "delete"],
                                    "description": "Type d'opération : add (ajouter), replace (remplacer), delete (supprimer)"
                                },
                                "anchor_before": {
                                    "type": "string",
                                    "description": "🔗 12+ caractères QUI PRÉCÈDENT la zone à modifier. La zone commence APRÈS cette ancre. Null = début du texte."
                                },
                                "anchor_after": {
                                    "type": "string",
                                    "description": "🔗 12+ caractères QUI SUIVENT la zone à modifier. La zone finit AVANT cette ancre. Null = fin du texte."
                                },
                                "new_content": {
                                    "type": "string",
                                    "description": "Nouveau contenu (pour add/replace, vide pour delete)"
                                }
                            },
                            "required": ["operation"]
                        }
                    },
                    "preview_only": {
                        "type": "boolean",
                        "description": "Si true, génère uniquement une prévisualisation (défaut: false)",
                        "default": False
                    }
                },
                "required": ["context_type", "operations"]
            }
        }
    
    async def update_context(
        self,
        context_type: str,
        operations: List[Dict],
        service_name: str = None,
        preview_only: bool = False,
        require_approval: bool = True  # 🆕 Par défaut, demander approbation
    ) -> Dict:
        """
        Met à jour un contexte en utilisant le TextUpdaterAgent.
        
        🆕 WORKFLOW AVEC APPROBATION :
        1. Applique les opérations reçues via TextUpdaterAgent
        2. Si require_approval=True → Demande approbation via carte interactive
        3. Stocke proposition (pour PUBLISH_CONTEXT)
        4. Retourne statut (pending_approval/rejected/approved)
        
        Args:
            context_type: Type de contexte (router/accounting/company)
            operations: Liste d'opérations de modification générées par l'agent
            service_name: Nom du service (requis si context_type=router)
            preview_only: Si true, prévisualisation uniquement
            require_approval: Si True, demande approbation (défaut: True)
        
        Returns:
            Dict avec résumé de la modification et ID de proposition
        """
        try:
            logger.info(
                f"[UPDATE_CONTEXT] Type={context_type}, service={service_name}, "
                f"preview={preview_only}, require_approval={require_approval}"
            )
            
            if not self.mandate_path:
                return {
                    "success": False,
                    "error": "mandate_path non configuré"
                }
            
            # Validation : service_name requis pour router
            if context_type == "router" and not service_name:
                return {
                    "success": False,
                    "error": "Le paramètre 'service_name' est requis pour context_type='router'"
                }
            
            # Récupérer le contexte actuel
            all_contexts = self.firebase_management.get_all_contexts(self.mandate_path)
            
            if not all_contexts:
                return {
                    "success": False,
                    "error": "Impossible de récupérer les contextes depuis Firebase"
                }
            
            # Extraire le texte à modifier selon le type
            original_text = ""
            context_source = ""
            
            if context_type == "router":
                router_context = all_contexts.get("router", {})
                router_prompt_data = router_context.get("router_prompt", {})
                original_text = router_prompt_data.get(service_name, "")
                context_source = f"router_prompt/{service_name}"
                
                # ✅ Gérer le cas d'un contexte vide (permettre création via "add")
                if not original_text:
                    # Vérifier si toutes les opérations sont des "add"
                    all_operations_are_add = all(
                        op.get("operation") == "add" 
                        for op in operations
                    )
                    
                    if not all_operations_are_add:
                        return {
                            "success": False,
                            "error": f"Service '{service_name}' est vide - seules les opérations 'add' sont autorisées pour créer du contenu"
                        }
                    
                    logger.info(f"[UPDATE_CONTEXT] Service '{service_name}' vide, création de contenu via 'add'")
                    original_text = ""  # Travailler sur chaîne vide
            
            elif context_type == "accounting":
                accounting_context = all_contexts.get("accounting", {})
                original_text = accounting_context.get("accounting_context_0", "")
                context_source = "accounting_context/data/accounting_context_0"
                
                # ✅ Gérer le cas d'un contexte vide (permettre création via "add")
                if not original_text:
                    # Vérifier si toutes les opérations sont des "add"
                    all_operations_are_add = all(
                        op.get("operation") == "add" 
                        for op in operations
                    )
                    
                    if not all_operations_are_add:
                        return {
                            "success": False,
                            "error": "accounting_context_0 est vide - seules les opérations 'add' sont autorisées pour créer du contenu"
                        }
                    
                    logger.info("[UPDATE_CONTEXT] accounting_context_0 vide, création de contenu via 'add'")
                    original_text = ""  # Travailler sur chaîne vide
            
            elif context_type == "bank":
                bank_context = all_contexts.get("bank", {})
                original_text = bank_context.get("bank_context_0", "")
                context_source = "bank_context/data/bank_context_0"
                
                # ✅ Gérer le cas d'un contexte vide (permettre création via "add")
                if not original_text:
                    all_operations_are_add = all(
                        op.get("operation") == "add"
                        for op in operations
                    )
                    if not all_operations_are_add:
                        return {
                            "success": False,
                            "error": "bank_context_0 est vide - seules les opérations 'add' sont autorisées pour créer du contenu"
                        }
                    logger.info("[UPDATE_CONTEXT] bank_context_0 vide, création de contenu via 'add'")
                    original_text = ""

            elif context_type == "company":
                general_context = all_contexts.get("general", {})
                original_text = general_context.get("context_company_profile_report", "")
                context_source = "general_context/context_company_profile_report"
                
                # ✅ Gérer le cas d'un contexte vide (permettre création via "add")
                if not original_text:
                    # Vérifier si toutes les opérations sont des "add"
                    all_operations_are_add = all(
                        op.get("operation") == "add" 
                        for op in operations
                    )
                    
                    if not all_operations_are_add:
                        return {
                            "success": False,
                            "error": "context_company_profile_report est vide - seules les opérations 'add' sont autorisées pour créer du contenu"
                        }
                    
                    logger.info("[UPDATE_CONTEXT] context_company_profile_report vide, création de contenu via 'add'")
                    original_text = ""  # Travailler sur chaîne vide
            
            # Initialiser le text_updater
            self._init_text_updater()
            
            # Validation des opérations reçues
            if not operations or not isinstance(operations, list):
                return {
                    "success": False,
                    "error": "Le paramètre 'operations' doit être une liste non vide"
                }
            
            # 🆕 VALIDATION AVEC SYSTÈME D'ANCRES (anchor_before / anchor_after)
            for i, op in enumerate(operations):
                if not isinstance(op, dict):
                    return {
                        "success": False,
                        "error": f"Opération {i+1} : doit être un dictionnaire"
                    }
                
                operation = op.get("operation")
                anchor_before = op.get("anchor_before")
                anchor_after = op.get("anchor_after")
                new_content = op.get("new_content", "")
                
                # Validation : 'operation' est requis
                if not operation:
                    return {
                        "success": False,
                        "error": f"Opération {i+1} : 'operation' est requis (add, replace, delete)"
                    }
                
                if operation not in ["add", "replace", "delete"]:
                    return {
                        "success": False,
                        "error": f"Opération {i+1} : operation='{operation}' invalide. Utilisez 'add', 'replace', ou 'delete'"
                    }
                
                # Validation : Pour replace/delete, au moins une ancre est requise
                if operation in ["replace", "delete"]:
                    if anchor_before is None and anchor_after is None:
                        return {
                            "success": False,
                            "error": (
                                f"Opération {i+1} : Pour '{operation}', au moins une ancre est requise. "
                                f"🎯 Fournissez 'anchor_before' (12+ caractères AVANT la zone) "
                                f"et/ou 'anchor_after' (12+ caractères APRÈS la zone)."
                            ),
                            "operation_index": i,
                            "operation": op
                        }
                
                # Validation : new_content requis pour add/replace
                if operation in ["add", "replace"]:
                    if new_content is None:
                        return {
                            "success": False,
                            "error": f"Opération {i+1} : 'new_content' est requis pour operation='{operation}'"
                        }
                
                # Validation : Ancres doivent avoir une longueur minimale (recommandé 12+)
                MIN_ANCHOR_LENGTH = 8  # Minimum 8 caractères
                if anchor_before and len(anchor_before) < MIN_ANCHOR_LENGTH:
                    logger.warning(
                        f"[UPDATE_CONTEXT] Opération {i+1} : anchor_before trop court ({len(anchor_before)} chars). "
                        f"Recommandé: 12+ caractères pour éviter les ambiguïtés."
                    )
                if anchor_after and len(anchor_after) < MIN_ANCHOR_LENGTH:
                    logger.warning(
                        f"[UPDATE_CONTEXT] Opération {i+1} : anchor_after trop court ({len(anchor_after)} chars). "
                        f"Recommandé: 12+ caractères pour éviter les ambiguïtés."
                    )
            
            # 🆕 Appliquer les opérations avec le NOUVEAU système d'ancres
            update_result = self.text_updater.apply_operations_v2(
                text_to_update=original_text,
                operations_list=operations
            )
            
            if not update_result.get("success"):
                return {
                    "success": False,
                    "error": f"Échec de la mise à jour: {update_result.get('error')}",
                    "operations_log": update_result.get("operations_log", [])
                }
            
            updated_text = update_result.get("updated_text", "")
            operations_log = update_result.get("operations_log", [])
            
            # Générer un ID de proposition
            import uuid
            import hashlib
            proposal_id = f"proposal_{context_type}_{uuid.uuid4().hex[:8]}"
            
            # Calculer hash pour détection de changements
            text_hash = hashlib.sha256(original_text.encode()).hexdigest()[:12]
            
            # ═══ ÉTAPE 2 : Stocker proposition (pour le workflow d'approbation) ═══
            # Utiliser brain.context_proposal pour stocker temporairement
            # (Le brain est accessible via self.brain dans ContextTools)
            self.brain.context_proposal = {
                "proposal_id": proposal_id,
                "context_type": context_type,
                "context_source": context_source,
                "service_name": service_name,
                "original_text": original_text,
                "original_hash": text_hash,
                "updated_text": updated_text,
                "operations_log": operations_log,
                "operations_requested": operations,  # Stocker les opérations originales
                "preview_only": preview_only,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending_approval" if require_approval else "approved"
            }
            
            logger.info(
                f"[UPDATE_CONTEXT] Proposition créée: {proposal_id} "
                f"(status={'pending_approval' if require_approval else 'approved'})"
            )
            
            # ═══ ÉTAPE 3 : Demander approbation si requis ═══
            if require_approval and not preview_only:
                logger.info(
                    f"[UPDATE_CONTEXT] 🃏 Demande approbation pour "
                    f"modification {context_type}"
                )
                
                # Détecter warnings
                warnings = []
                failed_ops = [op for op in operations_log if not op.get("success")]
                if failed_ops:
                    warnings.append(
                        f"⚠️ {len(failed_ops)} opération(s) ont échoué lors de la mise à jour"
                    )
                
                # 🆕 APPEL AU SYSTÈME D'APPROBATION
                from ...llm_service.llm_manager import get_llm_manager
                
                llm_manager = get_llm_manager()
                
                # Récupérer thread_key depuis brain
                thread_key = self.brain.active_thread_key
                
                if not thread_key:
                    logger.error(
                        "[UPDATE_CONTEXT] ❌ active_thread_key non défini dans brain. "
                        "Impossible d'envoyer carte d'approbation."
                    )
                    # Fallback: approuver automatiquement
                    self.brain.context_proposal["status"] = "approved"
                    self.brain.context_proposal["auto_approved_reason"] = "thread_key_missing"
                else:
                    try:
                        approval_result = await llm_manager.request_approval_with_card(
                            user_id=self.firebase_user_id,
                            collection_name=self.collection_name,
                            thread_key=thread_key,
                            card_type="text_modification_approval",
                            card_params={
                                "context_type": context_type,
                                "original_text": original_text,
                                "operations_log": operations_log,
                                "final_text": updated_text,
                                "warnings": warnings
                            },
                            timeout=900  # 15 minutes
                        )
                        
                        # Mettre à jour le statut de la proposition
                        if approval_result.get("approved"):
                            self.brain.context_proposal["status"] = "approved"
                            self.brain.context_proposal["approved_at"] = datetime.now(timezone.utc).isoformat()
                            self.brain.context_proposal["user_comment"] = approval_result.get("user_message", "")
                            
                            logger.info(f"[UPDATE_CONTEXT] ✅ Modification approuvée")
                            
                            # 🆕 SAUVEGARDER AUTOMATIQUEMENT DANS FIREBASE
                            logger.info(f"[UPDATE_CONTEXT] 💾 Sauvegarde automatique dans Firebase...")
                            
                            save_result = await self._save_context_to_firebase(
                                context_type=context_type,
                                service_name=service_name,
                                updated_text=updated_text
                            )
                            
                            if save_result.get("success"):
                                # Nettoyer la proposition après sauvegarde réussie
                                self.brain.context_proposal = None
                                
                                logger.info(
                                    f"[UPDATE_CONTEXT] ✅ Sauvegarde réussie - "
                                    f"path={save_result.get('context_path')}"
                                )
                                
                                return {
                                    "success": True,
                                    "status": "published",  # ← Nouveau statut !
                                    "message": f"✅ Modification de {context_type} approuvée et sauvegardée dans Firebase",
                                    "proposal_id": proposal_id,
                                    "operations_count": len(operations_log),
                                    "user_comment": approval_result.get("user_message", ""),
                                    "context_path": save_result.get("context_path"),
                                    "last_refresh": save_result.get("last_refresh")
                                }
                            else:
                                # Sauvegarde a échoué malgré l'approbation
                                logger.error(
                                    f"[UPDATE_CONTEXT] ❌ Échec sauvegarde: "
                                    f"{save_result.get('error')}"
                                )
                                return {
                                    "success": False,
                                    "status": "approved_but_save_failed",
                                    "message": f"⚠️ Modification approuvée mais échec de sauvegarde Firebase",
                                    "proposal_id": proposal_id,
                                    "save_error": save_result.get("error")
                                }
                        else:
                            # Modification refusée par l'utilisateur
                            user_comment = approval_result.get("user_message", "")
                            is_timeout = approval_result.get("timeout", False)
                            
                            self.brain.context_proposal["status"] = "rejected"
                            self.brain.context_proposal["rejected_at"] = datetime.now(timezone.utc).isoformat()
                            self.brain.context_proposal["rejection_reason"] = user_comment
                            
                            # Logger le refus avec le commentaire
                            if is_timeout:
                                logger.warning(
                                    f"[UPDATE_CONTEXT] ⏰ Timeout - Aucune réponse après 15 minutes"
                                )
                            else:
                                logger.info(
                                    f"[UPDATE_CONTEXT] ❌ Modification refusée - "
                                    f"Commentaire: {user_comment if user_comment else 'Aucun'}"
                                )
                            
                            # Nettoyer la proposition (ne pas garder en mémoire)
                            self.brain.context_proposal = None
                            
                            return {
                                "success": False,
                                "status": "rejected",
                                "message": f"❌ Modification de {context_type} refusée par l'utilisateur",
                                "proposal_id": proposal_id,
                                "rejection_reason": user_comment,
                                "rejected_at": datetime.now(timezone.utc).isoformat(),
                                "timeout": is_timeout
                            }
                    
                    except Exception as e:
                        logger.error(f"[UPDATE_CONTEXT] Erreur approbation: {e}", exc_info=True)
                        # Fallback: approuver automatiquement en cas d'erreur
                        self.brain.context_proposal["status"] = "approved"
                        self.brain.context_proposal["auto_approved_reason"] = f"approval_error: {str(e)}"
            else:
                # Pas d'approbation requise ou preview_only
                logger.info(
                    f"[UPDATE_CONTEXT] Aucune approbation requise "
                    f"(require_approval={require_approval}, preview_only={preview_only})"
                )
            
            # Retourner seulement un RÉSUMÉ (pour ne pas surcharger l'historique)
            return {
                "success": True,
                "proposal_id": proposal_id,
                "context_type": context_type,
                "service_name": service_name if service_name else "N/A",
                "original_hash": text_hash,
                "original_length": len(original_text),
                "original_preview": original_text[:300] + "..." if len(original_text) > 300 else original_text,
                "updated_length": len(updated_text),
                "updated_preview": updated_text[:300] + "..." if len(updated_text) > 300 else updated_text,
                "operations_count": len(operations_log),
                "operations_summary": [
                    f"• {op.get('args_from_llm', {}).get('operation', '?').upper()} sur {op.get('args_from_llm', {}).get('section_type', '?')}"
                    for op in operations_log[:5]  # Max 5 premières opérations
                ],
                "preview_only": preview_only,
                "status": self.brain.context_proposal.get("status", "unknown"),
                "summary": f"✏️ Modification préparée: {len(operations_log)} opération(s) sur {context_type}",
                "next_step": "🃏 Carte d'approbation envoyée - En attente de validation" if not preview_only else "👁️ Prévisualisation uniquement"
            }
        
        except Exception as e:
            logger.error(f"[UPDATE_CONTEXT] Erreur: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    # 🗑️ PUBLISH_CONTEXT a été supprimé - La sauvegarde se fait automatiquement dans UPDATE_CONTEXT après approbation
