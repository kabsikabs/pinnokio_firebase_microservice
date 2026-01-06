# Journal des Commits et Push

Ce dossier contient le journal de tous les commits et push effectués sur ce repository.

## 📋 Format du Journal

Chaque entrée de journal doit suivre ce format :

```
## [Date] - [Titre du commit]

**Hash:** `[hash_du_commit]`  
**Auteur:** [nom_auteur]  
**Date:** [date_et_heure]  
**Message:** [message_du_commit]

### Modifications
- [Description détaillée des changements]
- [Fichiers modifiés/ajoutés/supprimés]
- [Impact sur le projet]

### Notes
[Notes supplémentaires si nécessaire]
```

## 📝 Instructions pour Mettre à Jour le Journal

### 1. Après chaque commit/push

1. Récupérer les informations du commit :
   ```bash
   git log -1 --pretty=format:"%H|%an|%ai|%s"
   ```

2. Créer ou mettre à jour le fichier journal pour la date correspondante :
   - Format du nom de fichier : `YYYY-MM-DD.md`
   - Exemple : `2025-01-03.md`

3. Ajouter une nouvelle entrée en haut du fichier (les plus récents en premier)

### 2. Structure du fichier journal

Chaque fichier journal (ex: `2025-01-03.md`) doit contenir :

```markdown
# Journal des Commits - [Date]

## [Heure] - [Titre]

**Hash:** `abc1234`  
**Auteur:** nom_auteur  
**Date:** 2025-01-03 14:30:00  
**Message:** Description du commit

### Modifications
- Détail des changements

### Notes
- Notes optionnelles
```

### 3. Commandes utiles

#### Récupérer tous les commits d'aujourd'hui :
```bash
git log --since="today" --pretty=format:"%H|%an|%ai|%s" --no-color
```

#### Récupérer les commits d'une date spécifique :
```bash
git log --since="2025-01-03 00:00:00" --until="2025-01-03 23:59:59" --pretty=format:"%H|%an|%ai|%s" --no-color
```

#### Récupérer les commits de la semaine :
```bash
git log --since="1 week ago" --pretty=format:"%H|%an|%ai|%s" --no-color
```

## ✅ Checklist avant de push

- [ ] Commit effectué avec un message clair
- [ ] Journal mis à jour avec les détails du commit
- [ ] Fichier journal créé/mis à jour pour la date du jour
- [ ] Toutes les modifications documentées

## 📁 Organisation

- Un fichier par jour : `YYYY-MM-DD.md`
- Les commits les plus récents en haut de chaque fichier
- Format cohérent pour faciliter la recherche et la lecture


