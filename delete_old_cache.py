from app.redis_client import get_redis

redis_client = get_redis()

user_id = "4BHlZ7YMYMXicWIYRYsqEkXcnzL2"
company_id = "klk_space_id_8b2dce"

keys_to_delete = [
    f"cache:{user_id}:{company_id}:router:documents",
    f"cache:{user_id}:{company_id}:bank:transactions",
]

print("🗑️ SUPPRESSION DES CLÉS REDIS OBSOLÈTES")
print("="*80)

for key in keys_to_delete:
    result = redis_client.delete(key)
    if result:
        print(f"✅ Supprimé: {key}")
    else:
        print(f"⚠️ Clé introuvable: {key}")

print("="*80)
print("✅ TERMINÉ")
print("\n🔄 Maintenant, rafraîchis l'UI Reflex pour que le cache soit recréé avec les bonnes données.")

