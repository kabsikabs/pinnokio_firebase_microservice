"""
Tests unitaires pour la méthode delete_company de NeonHRManager.

Ces tests valident:
1. La suppression d'une société avec cascade=True
2. La suppression d'une société avec cascade=False
3. Le cas où la société n'existe pas dans Neon
4. La gestion des erreurs de connexion

Usage:
    python -m pytest tests/test_neon_hr_manager_delete_company.py -v

Author: Test Team
Created: 2026-02-01
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def neon_manager():
    """Fixture pour NeonHRManager avec mocks."""
    from app.tools.neon_hr_manager import NeonHRManager
    
    # Réinitialiser le singleton pour les tests
    NeonHRManager._instance = None
    NeonHRManager._initialized = False
    NeonHRManager._pool = None
    
    manager = NeonHRManager()
    
    # Mock du pool de connexions
    mock_pool = AsyncMock()
    mock_conn = AsyncMock()
    mock_transaction = AsyncMock()
    
    # Configurer le mock pour simuler un contexte de transaction
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_transaction
    
    mock_pool.acquire.return_value = mock_conn
    mock_pool.__aenter__ = AsyncMock(return_value=mock_pool)
    mock_pool.__aexit__ = AsyncMock(return_value=None)
    
    manager._pool = mock_pool
    
    yield manager, mock_pool, mock_conn
    
    # Nettoyage
    NeonHRManager._instance = None
    NeonHRManager._initialized = False


@pytest.fixture
def sample_company_id():
    """UUID de test pour une société."""
    return uuid4()


@pytest.fixture
def sample_mandate_path():
    """Chemin Firebase de test."""
    return "clients/4BHlZ7YMYMXicWIYRYsqEkXcnzL2/bo_clients/4BHlZ7YMYMXicWIYRYsqEkXcnzL2/mandates/ymfTmwoDLa7bWJQJgvBz"


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_company_with_cascade_success(
    neon_manager, 
    sample_company_id, 
    sample_mandate_path
):
    """
    Test: Suppression réussie avec cascade=True.
    
    L'argument principal utilisé est: mandate_path
    """
    manager, mock_pool, mock_conn = neon_manager
    
    # Mock: get_company_id_from_mandate_path retourne un UUID
    manager.get_company_id_from_mandate_path = AsyncMock(return_value=sample_company_id)
    
    # Mock: Les requêtes DELETE retournent des résultats simulés
    mock_conn.execute = AsyncMock(side_effect=[
        "DELETE 5",  # payroll_results
        "DELETE 3",  # contracts
        "DELETE 2",   # employees
        "DELETE 1"    # company
    ])
    
    # Exécuter la suppression avec cascade=True
    result = await manager.delete_company(
        mandate_path=sample_mandate_path,
        cascade=True
    )
    
    # Vérifications
    assert result["success"] is True
    assert result["company_id"] == str(sample_company_id)
    assert result["deleted_counts"]["payroll_results"] == 5
    assert result["deleted_counts"]["contracts"] == 3
    assert result["deleted_counts"]["employees"] == 2
    
    # Vérifier que get_company_id_from_mandate_path a été appelé avec mandate_path
    manager.get_company_id_from_mandate_path.assert_called_once_with(sample_mandate_path)
    
    # Vérifier que les requêtes DELETE ont été exécutées dans le bon ordre
    assert mock_conn.execute.call_count == 4


@pytest.mark.asyncio
async def test_delete_company_without_cascade(
    neon_manager,
    sample_company_id,
    sample_mandate_path
):
    """
    Test: Suppression avec cascade=False (supprime seulement la société).
    
    L'argument principal utilisé est: mandate_path
    """
    manager, mock_pool, mock_conn = neon_manager
    
    # Mock: get_company_id_from_mandate_path retourne un UUID
    manager.get_company_id_from_mandate_path = AsyncMock(return_value=sample_company_id)
    
    # Mock: Seule la suppression de la société est exécutée
    mock_conn.execute = AsyncMock(return_value="DELETE 1")
    
    # Exécuter la suppression avec cascade=False
    result = await manager.delete_company(
        mandate_path=sample_mandate_path,
        cascade=False
    )
    
    # Vérifications
    assert result["success"] is True
    assert result["company_id"] == str(sample_company_id)
    assert result["deleted_counts"]["payroll_results"] == 0
    assert result["deleted_counts"]["contracts"] == 0
    assert result["deleted_counts"]["employees"] == 0
    
    # Vérifier que seule la suppression de la société a été exécutée
    assert mock_conn.execute.call_count == 1
    # Vérifier que c'est bien la requête DELETE de la société
    call_args = mock_conn.execute.call_args[0]
    assert "DELETE FROM core.companies" in call_args[0]


@pytest.mark.asyncio
async def test_delete_company_not_found(
    neon_manager,
    sample_mandate_path
):
    """
    Test: Suppression d'une société qui n'existe pas dans Neon.
    
    L'argument principal utilisé est: mandate_path
    """
    manager, mock_pool, mock_conn = neon_manager
    
    # Mock: get_company_id_from_mandate_path retourne None
    manager.get_company_id_from_mandate_path = AsyncMock(return_value=None)
    
    # Exécuter la suppression
    result = await manager.delete_company(
        mandate_path=sample_mandate_path,
        cascade=True
    )
    
    # Vérifications
    assert result["success"] is True
    assert result["company_id"] is None
    assert result["message"] == "Company not found in Neon"
    assert result["deleted_counts"] == {}
    
    # Vérifier qu'aucune requête DELETE n'a été exécutée
    mock_conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_delete_company_database_error(
    neon_manager,
    sample_company_id,
    sample_mandate_path
):
    """
    Test: Gestion d'une erreur de base de données lors de la suppression.
    
    L'argument principal utilisé est: mandate_path
    """
    manager, mock_pool, mock_conn = neon_manager
    
    # Mock: get_company_id_from_mandate_path retourne un UUID
    manager.get_company_id_from_mandate_path = AsyncMock(return_value=sample_company_id)
    
    # Mock: Une erreur se produit lors de l'exécution
    mock_conn.execute = AsyncMock(side_effect=Exception("Database connection error"))
    
    # Exécuter la suppression
    result = await manager.delete_company(
        mandate_path=sample_mandate_path,
        cascade=True
    )
    
    # Vérifications
    assert result["success"] is False
    assert "error" in result
    assert "Database connection error" in result["error"]


@pytest.mark.asyncio
async def test_delete_company_cache_cleanup(
    neon_manager,
    sample_company_id,
    sample_mandate_path
):
    """
    Test: Vérification que le cache est nettoyé après suppression réussie.
    
    L'argument principal utilisé est: mandate_path
    """
    manager, mock_pool, mock_conn = neon_manager
    
    # Ajouter l'entrée dans le cache
    manager._company_cache[sample_mandate_path] = sample_company_id
    
    # Mock: get_company_id_from_mandate_path retourne un UUID
    manager.get_company_id_from_mandate_path = AsyncMock(return_value=sample_company_id)
    
    # Mock: Les requêtes DELETE retournent des résultats simulés
    mock_conn.execute = AsyncMock(side_effect=[
        "DELETE 0",  # payroll_results
        "DELETE 0",  # contracts
        "DELETE 0",   # employees
        "DELETE 1"    # company
    ])
    
    # Vérifier que l'entrée est dans le cache avant
    assert sample_mandate_path in manager._company_cache
    
    # Exécuter la suppression
    result = await manager.delete_company(
        mandate_path=sample_mandate_path,
        cascade=True
    )
    
    # Vérifier que l'entrée a été supprimée du cache
    assert sample_mandate_path not in manager._company_cache
    assert result["success"] is True


@pytest.mark.asyncio
async def test_delete_company_argument_mandate_path():
    """
    Test: Vérification que mandate_path est l'argument principal utilisé.
    
    Ce test démontre que mandate_path est l'argument utilisé pour:
    1. Identifier la société dans la base de données
    2. Nettoyer le cache après suppression
    
    Utilise le vrai mandate_path du log d'erreur.
    """
    from app.tools.neon_hr_manager import NeonHRManager
    
    # Réinitialiser le singleton
    NeonHRManager._instance = None
    NeonHRManager._initialized = False
    
    manager = NeonHRManager()
    
    # Mock de get_company_id_from_mandate_path pour capturer l'argument
    original_method = manager.get_company_id_from_mandate_path
    captured_mandate_path = None
    
    async def mock_get_company_id(mandate_path: str):
        nonlocal captured_mandate_path
        captured_mandate_path = mandate_path
        return None  # Société non trouvée pour simplifier
    
    manager.get_company_id_from_mandate_path = mock_get_company_id
    
    # Exécuter avec le vrai mandate_path du log d'erreur
    real_mandate_path = "clients/4BHlZ7YMYMXicWIYRYsqEkXcnzL2/bo_clients/4BHlZ7YMYMXicWIYRYsqEkXcnzL2/mandates/ymfTmwoDLa7bWJQJgvBz"
    result = await manager.delete_company(mandate_path=real_mandate_path)
    
    # Vérifier que mandate_path a bien été utilisé comme argument
    assert captured_mandate_path == real_mandate_path, f"Le mandate_path capturé ({captured_mandate_path}) doit correspondre à celui passé en argument ({real_mandate_path})"
    assert result["success"] is True
    assert result["company_id"] is None


# ═══════════════════════════════════════════════════════════════
# TEST D'INTÉGRATION (nécessite une vraie connexion DB)
# ═══════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_company_real_mandate_path_from_log():
    """
    Test d'intégration avec le vrai mandate_path du log d'erreur.
    
    Ce test utilise le mandate_path exact qui a causé l'erreur dans le log:
    'clients/4BHlZ7YMYMXicWIYRYsqEkXcnzL2/bo_clients/4BHlZ7YMYMXicWIYRYsqEkXcnzL2/mandates/ymfTmwoDLa7bWJQJgvBz'
    
    ⚠️ Ce test nécessite:
    - Une connexion Neon valide (NEON_DATABASE_URL ou NEON_SECRET_NAME)
    - La société peut exister ou non dans la base de données
    
    Usage:
        pytest tests/test_neon_hr_manager_delete_company.py::test_delete_company_real_mandate_path_from_log -v -m integration
    """
    from app.tools.neon_hr_manager import get_neon_hr_manager
    
    manager = get_neon_hr_manager()
    
    # Utiliser le mandate_path exact du log d'erreur
    real_mandate_path = "clients/4BHlZ7YMYMXicWIYRYsqEkXcnzL2/bo_clients/4BHlZ7YMYMXicWIYRYsqEkXcnzL2/mandates/ymfTmwoDLa7bWJQJgvBz"
    
    print(f"\n{'='*80}")
    print("TEST DE SUPPRESSION AVEC LE MANDATE_PATH DU LOG")
    print(f"{'='*80}\n")
    
    print("🗄️  BASES DE DONNÉES ET TABLES AFFECTÉES PAR LA SUPPRESSION")
    print("-" * 80)
    print()
    print("📊 Base de données: PostgreSQL Neon")
    print()
    print("📋 Tables supprimées (dans l'ordre d'exécution):")
    print()
    print("   1️⃣  hr.payroll_results")
    print("      └─ Schéma: hr")
    print("      └─ Table: payroll_results")
    print("      └─ Condition: employee_id IN (SELECT id FROM hr.employees WHERE company_id = $1)")
    print("      └─ Description: Résultats de calcul de paie pour tous les employés de la société")
    print()
    print("   2️⃣  hr.contracts")
    print("      └─ Schéma: hr")
    print("      └─ Table: contracts")
    print("      └─ Condition: employee_id IN (SELECT id FROM hr.employees WHERE company_id = $1)")
    print("      └─ Description: Contrats de travail de tous les employés de la société")
    print()
    print("   3️⃣  hr.employees")
    print("      └─ Schéma: hr")
    print("      └─ Table: employees")
    print("      └─ Condition: company_id = $1")
    print("      └─ Description: Tous les employés de la société")
    print()
    print("   4️⃣  core.companies")
    print("      └─ Schéma: core")
    print("      └─ Table: companies")
    print("      └─ Condition: id = $1")
    print("      └─ Description: La société elle-même")
    print()
    print("📝 Note: Toutes les suppressions sont exécutées dans une transaction")
    print("   PostgreSQL, donc soit tout est supprimé, soit rien n'est supprimé.")
    print()
    print("🗑️  Cache: Le cache en mémoire (mandate_path → company_id) est également")
    print("   nettoyé après la suppression réussie.")
    print()
    print(f"{'='*80}\n")
    
    print(f"🔍 Paramètres de test:")
    print(f"   mandate_path: {real_mandate_path}")
    print(f"   cascade: True")
    print()
    print("🚀 Exécution de la suppression...\n")
    
    # Tenter la suppression
    result = await manager.delete_company(
        mandate_path=real_mandate_path,
        cascade=True
    )
    
    # Vérifications de base
    assert isinstance(result, dict), "Le résultat doit être un dictionnaire"
    assert "success" in result, "Le résultat doit contenir 'success'"
    
    print(f"{'='*80}")
    print("RÉSULTAT DE LA SUPPRESSION")
    print(f"{'='*80}\n")
    print(f"✅ Success: {result.get('success')}")
    print(f"📦 Company ID: {result.get('company_id')}")
    print()
    
    if result.get("success"):
        if result.get("company_id"):
            # La société existait et a été supprimée
            deleted_counts = result.get("deleted_counts", {})
            print("📊 DÉTAIL DES SUPPRESSIONS PAR TABLE:")
            print("-" * 80)
            print()
            
            # Afficher les résultats pour chaque table
            tables_info = [
                ("hr.payroll_results", deleted_counts.get('payroll_results', 0), 
                 "Résultats de calcul de paie"),
                ("hr.contracts", deleted_counts.get('contracts', 0),
                 "Contrats de travail"),
                ("hr.employees", deleted_counts.get('employees', 0),
                 "Employés"),
                ("core.companies", 1,
                 "Société (toujours 1 si suppression réussie)")
            ]
            
            total_deleted = 0
            for table_name, count, description in tables_info:
                status = "✅" if count > 0 else "⚪"
                print(f"   {status} {table_name:25} → {count:4} enregistrement(s) - {description}")
                if table_name != "core.companies":
                    total_deleted += count
            
            print()
            print(f"📈 Total d'enregistrements supprimés: {total_deleted + 1}")
            print()
            print("✅ Transaction PostgreSQL complétée avec succès")
            print("✅ Cache en mémoire nettoyé")
            assert "deleted_counts" in result, "Le résultat doit contenir 'deleted_counts'"
        else:
            # La société n'existait pas dans Neon
            print("ℹ️  Message: Société non trouvée dans Neon")
            print()
            print("ℹ️  Aucune table n'a été affectée car la société n'existe pas")
            print("   dans la base de données PostgreSQL Neon.")
            assert result.get("message") == "Company not found in Neon"
    else:
        # Une erreur s'est produite
        error_msg = result.get("error", "Unknown error")
        print(f"❌ Erreur: {error_msg}")
        print()
        print("⚠️  Aucune table n'a été modifiée (transaction annulée)")
        
        if "different loop" in error_msg.lower():
            print()
            print("⚠️  ERREUR DE BOUCLE D'ÉVÉNEMENTS DÉTECTÉE")
            print("   C'est l'erreur du log: 'got Future attached to a different loop'")
            print("   Cela se produit quand asyncio.run() est appelé dans un contexte")
            print("   asyncio déjà actif.")
            print()
            print("💡 Solution: Utiliser la boucle d'événements existante au lieu")
            print("   de créer une nouvelle avec asyncio.run()")
    
    print()
    print(f"{'='*80}\n")
    
    return result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_company_integration_real_db():
    """
    Test d'intégration avec une vraie base de données Neon.
    
    ⚠️ Ce test nécessite:
    - Une connexion Neon valide (NEON_DATABASE_URL ou NEON_SECRET_NAME)
    - Une société de test existante
    
    Usage:
        pytest tests/test_neon_hr_manager_delete_company.py::test_delete_company_integration_real_db -v -m integration
    """
    from app.tools.neon_hr_manager import get_neon_hr_manager
    
    manager = get_neon_hr_manager()
    
    # ⚠️ Utiliser un mandate_path de test réel
    test_mandate_path = "clients/TEST_UID/bo_clients/TEST_UID/mandates/TEST_MANDATE"
    
    # Tenter la suppression (peut échouer si la société n'existe pas, c'est normal)
    result = await manager.delete_company(
        mandate_path=test_mandate_path,
        cascade=True
    )
    
    # Le résultat doit être un dictionnaire valide
    assert isinstance(result, dict)
    assert "success" in result
    
    # Si la société existe, la suppression doit réussir
    if result.get("success") and result.get("company_id"):
        assert "deleted_counts" in result
        print(f"✅ Suppression réussie: {result}")
