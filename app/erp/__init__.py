"""
ERP Gateway Module — Couche d'abstraction multi-ERP.

Architecture:
- erp_provider.py : Interface abstraite ERPProvider + ERPResolver (mandate → provider)
- adapters/odoo_adapter.py : Implementation Odoo (XMLRPC)
- adapters/sage_adapter.py : Futur
- adapters/abacus_adapter.py : Futur

Premier client : JournalEntryAgent L3 (post_journal_entry).
Progressivement, les 37 methodes des workers seront migrees ici.
"""
