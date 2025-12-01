# ========================================
# Script pour corriger les problèmes de health check ECS
# ========================================

Write-Host "🔧 Correction de la configuration du health check..." -ForegroundColor Cyan

# 1. Augmenter le grace period du service ECS à 300 secondes (5 minutes)
Write-Host "`n📝 Étape 1: Augmentation du grace period ECS à 300 secondes..." -ForegroundColor Yellow

aws ecs update-service `
    --cluster pinnokio_cluster `
    --service pinnokio_microservice `
    --health-check-grace-period-seconds 300 `
    --region us-east-1

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Grace period ECS mis à jour avec succès!" -ForegroundColor Green
} else {
    Write-Host "❌ Erreur lors de la mise à jour du grace period" -ForegroundColor Red
    exit 1
}

# 2. Modifier le Target Group pour être plus tolérant
Write-Host "`n📝 Étape 2: Modification du Target Group..." -ForegroundColor Yellow

aws elbv2 modify-target-group `
    --target-group-arn arn:aws:elasticloadbalancing:us-east-1:654654322636:targetgroup/new-pinnokio-firebase-backend/6c7046f6f3969fee `
    --health-check-interval-seconds 30 `
    --health-check-timeout-seconds 10 `
    --healthy-threshold-count 2 `
    --unhealthy-threshold-count 5 `
    --region us-east-1

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Target Group mis à jour avec succès!" -ForegroundColor Green
} else {
    Write-Host "❌ Erreur lors de la mise à jour du Target Group" -ForegroundColor Red
    exit 1
}

# 3. Vérifier la nouvelle configuration
Write-Host "`n📊 Vérification de la nouvelle configuration..." -ForegroundColor Yellow

Write-Host "`n🔍 Configuration ECS Service:" -ForegroundColor Cyan
aws ecs describe-services `
    --cluster pinnokio_cluster `
    --services pinnokio_microservice `
    --region us-east-1 `
    --query "services[0].healthCheckGracePeriodSeconds" `
    --output text

Write-Host "`n🔍 Configuration Target Group:" -ForegroundColor Cyan
aws elbv2 describe-target-groups `
    --target-group-arns arn:aws:elasticloadbalancing:us-east-1:654654322636:targetgroup/new-pinnokio-firebase-backend/6c7046f6f3969fee `
    --region us-east-1 `
    --query "TargetGroups[0].[HealthCheckPath,HealthCheckIntervalSeconds,HealthCheckTimeoutSeconds,HealthyThresholdCount,UnhealthyThresholdCount]" `
    --output table

Write-Host "`n✨ Configuration corrigée!" -ForegroundColor Green
Write-Host "`nRécapitulatif des changements:" -ForegroundColor Cyan
Write-Host "  • Grace Period ECS: 60s → 300s (5 minutes)" -ForegroundColor White
Write-Host "  • Health Check Timeout: 5s → 10s" -ForegroundColor White
Write-Host "  • Healthy Threshold: 5 → 2 (démarre plus vite)" -ForegroundColor White
Write-Host "  • Unhealthy Threshold: 2 → 5 (plus tolérant)" -ForegroundColor White
Write-Host "`n⏳ Les nouvelles tâches utiliseront automatiquement ces paramètres" -ForegroundColor Yellow
Write-Host "💡 Les tâches existantes devraient maintenant être stables!" -ForegroundColor Green

