# CCTransfert

Transférer une session **Claude Code** d'une machine à une autre (Linux ↔ Linux,
Linux ↔ Windows), ou d'un dossier à un autre sur la même machine.

```
PC 1                                     PC 2
CCTransfert export   ──►  fichier .CCTransfert  ──►  CCTransfert import --to <dossier>
                          (clé USB, scp, cloud…)      cd <dossier> && claude -r
```

Après l'import, la session apparaît dans le picker `claude -r` du dossier choisi. Elle
se reprend comme si on n'avait pas changé de machine : chemins réécrits, sous-agents,
résultats d'outils, rewind (file-history) et plans compris.

- Python 3.9+, **bibliothèque standard uniquement**, aucune dépendance à installer.
- L'outil ne gère pas le transport du fichier : clé USB, `scp`, Nextcloud…, au choix.
- Le **code** du projet n'est pas transféré : synchronisez-le avec git.

Les détails du format de stockage de Claude Code sont dans [docs/FORMAT.md](docs/FORMAT.md).

---

## Installation

### Linux (et WSL)

Au choix :

```sh
# 1. Installation isolée (recommandée)
pipx install /chemin/vers/CCTransfert          # ou : uv tool install /chemin/vers/CCTransfert

# 2. Sans installation : lien vers le lanceur du dépôt
ln -s /chemin/vers/CCTransfert/bin/CCTransfert ~/.local/bin/CCTransfert
```

### Windows

Installez Python 3.9+ depuis python.org (cochez « Add to PATH », le lanceur `py` est
inclus), puis au choix :

```powershell
# 1. Installation isolée
py -m pip install --user pipx
py -m pipx install C:\chemin\vers\CCTransfert

# 2. Sans installation : ajoutez C:\chemin\vers\CCTransfert\bin au PATH, puis
CCTransfert.cmd doctor          # cmd.exe
.\bin\CCTransfert.ps1 doctor    # PowerShell
```

> **WSL** : CCTransfert doit tourner **dans le même environnement que Claude Code**.
> Si Claude Code est installé dans WSL, lancez CCTransfert dans WSL. S'il est installé
> sous Windows, lancez CCTransfert sous Windows. `CCTransfert doctor` détecte les cas
> douteux.

Vérification : `CCTransfert doctor`.

---

## Utilisation

### Workflow type

Sur le PC source, depuis le dossier du projet :

```sh
cd /home/alice/Projets/MonProjet
CCTransfert list                      # sessions de ce dossier
CCTransfert export                    # choix interactif dans la liste
# ou : CCTransfert export 2           # N° de la liste
# ou : CCTransfert export 1a2b3c4d    # début d'ID
# ou : CCTransfert export --latest -o ~/Transferts/
```

Transférez le fichier `.CCTransfert` produit, par exemple
`MonProjet_Ma-session_2026-10-16_1a2b3c4d.CCTransfert`.

Sur le PC cible :

```sh
CCTransfert inspect MonProjet_…_1a2b3c4d.CCTransfert                    # optionnel : voir le contenu
CCTransfert import MonProjet_…_1a2b3c4d.CCTransfert --dry-run --to /home/bob/Travail/Projets/App/MonProjet
CCTransfert import MonProjet_…_1a2b3c4d.CCTransfert --to /home/bob/Travail/Projets/App/MonProjet
cd /home/bob/Travail/Projets/App/MonProjet && claude -r
```

Sous Windows :

```powershell
CCTransfert import MonProjet_…_1a2b3c4d.CCTransfert --to "C:\Users\alice\Documents de travail\Projets\MonProjet"
cd "C:\Users\alice\Documents de travail\Projets\MonProjet"; claude -r
```

Sans `--to`, l'import propose trois choix :
- le dossier mémorisé lors d'un import précédent du même projet ;
- le dossier courant ;
- un chemin à saisir.

Si le dossier n'existe pas, il propose de le créer.

**Avant d'exporter, faites `/exit` dans la session** : l'outil prévient si elle est encore
en cours.

### Commandes

| Commande | Rôle |
|---|---|
| `doctor [--json]` | Diagnostic : OS, WSL, dossier de config, version de Claude Code, dossier temp, slug du dossier courant, vérification de l'algorithme sur les dossiers existants, convention de lettre de lecteur. |
| `list [--here \| --dir PATH \| --all]` | Sessions (N°, ID court, dernière activité, taille, titre, dossier). `●` = en cours, `[-p]` = créée par `claude -p` (absente du picker de Claude Code). |
| `export [ID\|NOM\|N°] [--dir PATH\|--all] [--latest] [-o FICHIER\|DOSSIER] [--with-memory] [--with-history] [--no-file-history] [--encrypt]` | Crée le bundle. Sans argument : choix interactif dans les sessions du dossier courant (`t` = toutes). |
| `import BUNDLE [--to DIR] [--map ANCIEN=NOUVEAU …] [--new-id] [--replace] [--dry-run] [--keep-mtime] [--calibrate] [-y]` | Importe et réécrit les chemins. |
| `inspect BUNDLE [--files]` | Résumé du bundle et vérification des sha256. |
| `undo [-y]` | Annule le dernier import (sauvegardes dans `~/.CCTransfert/backups/`). |
| `relocate SESSION --to DIR` | Déplace une session vers un autre dossier de la même machine. |

Options utiles :

- **`--with-memory`** (export) : inclut la mémoire du projet (`memory/`). À l'import,
  elle est fusionnée sans rien écraser. Un fichier existant au contenu différent est
  écrit à côté (`MEMORY.importe-<pc>.md`).
- **`--with-history`** (export) : inclut les prompts de la session dans
  `history.jsonl`, pour que la flèche ↑ les retrouve.
- **`--new-id`** (import) : crée une copie indépendante avec un nouvel identifiant.
  Indispensable pour importer une session sur la machine où elle existe déjà.
- **`--replace`** (import) : si le même ID existe déjà, l'ancienne version est
  sauvegardée puis remplacée, ou déplacée si elle est dans un autre dossier projet.
  C'est le cas de l'aller-retour A → B → A.
- **`--map ANCIEN=NOUVEAU`** : correspondance supplémentaire, par exemple un dépôt
  voisin cité dans la session :
  `--map /home/alice/Projets/MonProjet2=/home/bob/Travail/MonProjet2`.
- **`--keep-mtime`** : garde les dates d'origine. Par défaut, les transcripts reçoivent
  la date du jour, pour échapper au nettoyage automatique des sessions de plus de
  30 jours (`cleanupPeriodDays`).
- **`--dry-run`** : affiche tout ce qui serait écrit, avec des exemples de réécriture
  avant/après, sans rien toucher.
- **`--calibrate`** : lance `claude -p "ok"` dans le dossier cible pour relever le nom
  de dossier projet réellement utilisé, puis supprime cette session de test. Utile
  seulement pour les chemins de plus de 200 caractères. Cela coûte une petite
  requête API.
- **`--encrypt`** (export) : chiffre le bundle avec `age` ou `gpg` (phrase de passe).
  L'import le détecte et le déchiffre.

---

## Ce qui est transféré

| Transféré | Pas transféré |
|---|---|
| Transcript `projects/<slug>/<id>.jsonl` | `.credentials.json`, `~/.claude.json` (identifiants, confiance, MCP) |
| Dossier de session : sous-agents, résultats d'outils, titre | `settings.json`, serveurs MCP, hooks, skills |
| `file-history/<id>/` (rewind) | `sessions/`, `shell-snapshots/`, `session-env/`, caches |
| Plans (`plans/<nom>.md`) référencés | Scratchpad temporaire (`/tmp/claude-…`) |
| Listes de tâches (`tasks/<id>/`), anciens `todos/` | **Le code du projet** (utilisez git) |
| Optionnel : mémoire du projet, historique des prompts | |

Le bundle stocke les fichiers **tels quels**. Toute la réécriture se fait à l'import :
un même bundle peut donc aller vers n'importe quelle machine. À l'import, les sha256
sont vérifiés et les chemins dangereux du zip sont refusés (zip-slip).

> ⚠ Un transcript contient tout ce qui est passé dans la session : sorties de
> commandes, fichiers lus, et peut-être des secrets en clair. Transférez-le par un
> canal de confiance, ou utilisez `--encrypt`.

---

## Réécriture des chemins

L'import construit une table de correspondances, appliquée du préfixe le plus long au
plus court :

1. dossier du projet source → dossier cible ;
2. `~/.claude/projects/<ancien slug>` → `~/.claude/projects/<nouveau slug>` ;
3. dossier temp de Claude Code (`/tmp/claude-<uid>/<slug>`, ou sous Windows
   `%TEMP%\claude-0\<slug>`) ;
4. dossier de config, dossier temp, home ;
5. vos `--map`.

Exemple Linux → Windows :

| Avant | Après |
|---|---|
| `cwd` `/home/alice/Projets/MonProjet` | `C:\Users\alice\Documents de travail\Projets\MonProjet` |
| `file_path` `/home/alice/Projets/MonProjet/src/a.ts` | `C:\Users\alice\Documents de travail\Projets\MonProjet\src\a.ts` |
| commande `cd /home/alice/Projets/MonProjet && ls` | `cd C:/Users/alice/Documents de travail/Projets/MonProjet && ls` |
| `/home/alice/Projets/MonProjet2/x` (autre dépôt) | `C:\Users\alice\Projets\MonProjet2\x` (règle home) |

- Le JSON est parsé et réécrit **valeurs et clés de dictionnaire**. Les lignes non
  concernées restent identiques à l'octet près. Une ligne illisible est recopiée telle
  quelle, avec un avertissement.
- Une source Windows est reconnue sous toutes ses formes : `C:\…`, `C:/…`, `c:\…`,
  `C:\\…` et `/c/…` (Git Bash), sans tenir compte de la casse.
- Une frontière est exigée après chaque préfixe : `…/MonProjet` ne touche pas `…/MonProjet2`.
- **Jamais modifiés** :
  - les blocs `thinking` et les `signature`, qui sont signés : y toucher ferait échouer la reprise ;
  - les images base64 ;
  - les identifiants (`uuid`, `parentUuid`…) ;
  - le contenu des sauvegardes de rewind ;
  - les copies de requêtes API (`api-request*`), qui sont adressées par un hash.
- Avec `--new-id`, l'identifiant est remplacé partout : lignes, noms de fichiers et de
  dossiers, file-history, sous-agents, tâches.
- Le rapport final donne :
  - le nombre de remplacements par règle ;
  - des exemples avant/après ;
  - les chemins absolus restés tels quels (pour information).

---

## Placement sur la machine cible

Le nom du dossier projet (slug) est déterminé ainsi :
1. `CLAUDE_CODE_PROJECT_DIR_NAME`, s'il est défini avec `CLAUDE_CONFIG_DIR` ;
2. un dossier existant dont les transcripts ont ce `cwd` ;
3. le calcul de Claude Code, reproduit à l'identique, y compris le hash au-delà de
   200 caractères, avec la convention de lettre de lecteur (`C--` ou `c--`) observée
   sur la machine.

Gestion des conflits d'ID (une machine ne doit jamais avoir deux copies du même ID,
sinon `claude --resume <id>` répond « introuvable ») :

- **même dossier** (aller-retour) : remplacement, après confirmation ou avec `--replace` ;
- **autre dossier** : déplacement (avec `--replace` ou après confirmation) ou `--new-id` ;
- **session en cours** sur la machine cible : refus.

Tout ce qui est remplacé ou déplacé est sauvegardé dans `~/.CCTransfert/backups/`.
`CCTransfert undo` restaure. Si la session a été reprise depuis l'import, il prévient
que ces nouveaux échanges seront perdus.

---

## Limitations connues

- **Texte libre** (commandes, sorties, messages) : la conversion des séparateurs
  s'arrête au premier espace. Les noms de dossiers avec espaces déjà connus, comme
  `Documents de travail`, sont gérés. Un chemin Windows hors projet avec un espace
  inconnu peut garder des `\` après l'espace. C'est sans conséquence : ce n'est que
  de l'historique.
- Les chemins **relatifs** affichés (`displayPath`, `../../x`) ne sont pas réécrits.
- Les sessions créées par **`claude -p`** ou le SDK n'apparaissent pas dans le picker
  `claude -r` : c'est un choix de Claude Code. Reprenez-les avec
  `claude --resume <id>`.
- Au premier lancement dans le dossier cible, Claude Code demande de **faire confiance
  au dossier**. Les **serveurs MCP et settings** ne suivent pas.
- Le format des transcripts est interne et non documenté. L'outil parse de façon
  défensive et préserve les champs inconnus, mais une future version de Claude Code
  peut nécessiter une adaptation. Testé avec Claude Code 2.1.258 à 2.1.301.
- **Windows** : l'implémentation est testée par simulation depuis Linux. La validation
  sur un vrai PC Windows suit [docs/TEST-WINDOWS.md](docs/TEST-WINDOWS.md). Points
  encore à confirmer : le dossier temp (`claude-0` ou `claude`) et la casse de la
  lettre de lecteur.

---

## Développement

```sh
uv venv --python 3.9 .venv && uv pip install --python .venv/bin/python pytest
.venv/bin/python -m pytest
```

Les tests simulent des machines Linux et Windows dans des dossiers temporaires :
- transferts Linux ↔ Windows ;
- espaces, accents et points ;
- `MonProjet` / `MonProjet2` ;
- forme Git Bash ;
- clés de dictionnaire ;
- thinking intact octet par octet ;
- champs inconnus et lignes corrompues ;
- `--new-id`, conflits, aller-retour A → B → A identique à l'octet ;
- dry-run, undo, zip-slip, sha256 ;
- parcours complet de la CLI.

Organisation du code (`cctransfert/`) :

| Module | Rôle |
|---|---|
| `slug.py` | nom du dossier projet (algorithme de Claude Code) |
| `paths.py` | chemins posix/windows indépendamment de l'OS hôte |
| `profile.py` | profil d'une machine (home, config, temp, WSL) |
| `jsonio.py` | JSON fidèle (`JSON.stringify`), écritures atomiques |
| `rewrite.py` | moteur de réécriture |
| `store.py` | lecture des sessions |
| `bundle.py` | format `.CCTransfert` |
| `exporter.py`, `importer.py` | export, import, sauvegardes et undo |
| `doctor.py`, `cli.py`, `ui.py` | diagnostic et interface |
