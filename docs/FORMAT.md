# Stockage des sessions Claude Code : constats

Constats faits le 2026-10-16 sur Linux (Ubuntu, noyau 7.0), **Claude Code 2.1.301**
(CLI), avec des transcripts écrits par les versions 2.1.258 à 2.1.301 (CLI, extension
VS Code et application desktop partagent le même dossier de config).

Méthode : lecture de 38 transcripts réels (≈ 90 000 lignes), lecture du code
JavaScript embarqué dans le binaire `~/.local/share/claude/versions/2.1.301`, et
sessions de test `claude -p` dans des dossiers piégeux (supprimées ensuite).

Les points marqués **⚠ écart** contredisent ou complètent la description initiale.
Les points marqués **? Windows** restent à confirmer avec `CCTransfert doctor` sur le PC
Windows.

---

## 1. Dossier de configuration

```js
// code extrait du binaire 2.1.301 (noms minifiés)
configHome = (process.env.CLAUDE_CONFIG_DIR ?? join(homedir(), ".claude")).normalize("NFC")
projectsDir = join(configHome, "projects")
```

- Défaut `~/.claude`, remplacé par `$CLAUDE_CONFIG_DIR`. Le chemin est normalisé en
  **NFC**.
- `CLAUDE_CODE_PROJECT_DIR_NAME` n'est pris en compte que si `CLAUDE_CONFIG_DIR` est
  défini (ou en mode distant). Il doit respecter `^[A-Za-z0-9_-]{1,64}$` et ne pas être
  un nom réservé Windows (`con`, `prn`, `aux`, `nul`, `com0-9`, `lpt0-9`). Sinon, il est
  ignoré.

Contenu observé de `~/.claude` :

| Élément | Rôle | Transféré ? |
|---|---|---|
| `projects/<slug>/` | transcripts, sous-agents, résultats d'outils, mémoire | oui (session ; mémoire en option) |
| `file-history/<sid>/` | sauvegardes pour le rewind | oui |
| `plans/<nom>.md` | plans (mode plan) | oui, si référencés |
| `tasks/<listId>/` | listes de tâches (TaskCreate) | oui, si présentes |
| `todos/` | anciennes versions | oui, si présentes |
| `session-env/<sid>/` | scripts laissés par les hooks (`$CLAUDE_ENV_FILE`), exécutés avant chaque commande Bash (vides ici) | **non** (voir §7) |
| `sessions/<pid>.json` | sessions en cours | **jamais** |
| `history.jsonl` | historique des prompts (flèche ↑) | non (option possible, voir §9) |
| `.credentials.json`, `~/.claude.json` | auth, confiance, MCP | **jamais** |
| `shell-snapshots/`, `debug/`, `cache/`, `paste-cache/`, `telemetry/`, `ide/`, `daemon/`, `jobs/`, `backups/`… | caches et état local | **jamais** |

`todos/`, `tasks/` et `plans/` n'existent pas sur cette machine aujourd'hui
(`CLAUDE_CODE_ENABLE_TASKS=0`, plans déjà nettoyés). Leur emplacement vient du code.

## 2. Slug du dossier projet

### Algorithme (vérifié dans le code ET empiriquement)

```js
const MAX = 200
function sanitize(p) { return p.replace(/[^a-zA-Z0-9]/g, "-") }
function hash(s) {            // String.hashCode Java, entier signé 32 bits
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h << 5) - h + s.charCodeAt(i) | 0
  return h
}
function slug(p) {
  const s = sanitize(p)
  if (s.length <= MAX) return s
  return `${s.slice(0, MAX)}-${Math.abs(hash(p)).toString(36)}`
}
```

- **⚠ écart : le hash n'est pas inconnu.** C'est `String.hashCode` façon Java sur le
  chemin complet (non assaini), en valeur absolue, écrit en base 36. Testé : un chemin
  de 273 caractères donne bien le suffixe `-erxhkv` calculé par notre implémentation.
- **⚠ précision : remplacement par unité UTF-16**, pas par caractère. Un emoji
  (paire de substitution) donne `--`, un `é` décomposé (NFD) donne `e-`, un `é`
  composé (NFC) donne `-`.
- **Pas de normalisation Unicode du cwd** (contrairement au dossier de config) : un
  dossier NFD reste NFD dans `cwd` et dans le slug.
- **realpath confirmé** : lancé depuis un symlink, le slug et le `cwd` sont ceux de la
  cible du lien.
- Claude Code tolère un hash différent : pour un chemin de plus de 200 caractères, il
  scanne aussi tous les dossiers commençant par les 200 premiers caractères + `-` et
  garde ceux dont un transcript a un `cwd` (ou `relocatedCwd`) dont le slug
  correspond. `--calibrate` devient donc une simple vérification.

### Tests empiriques (2.1.301, sessions supprimées ensuite)

| Dossier de lancement | Slug observé | Calcul |
|---|---|---|
| `/tmp/CCTransfert test/Dossier.été_v2` | `-tmp-CCTransfert-test-Dossier--t--v2` | ✅ |
| `/tmp/CCTransfert test/nfd_e\u0301_emoji_😀` (NFD) | `-tmp-CCTransfert-test-nfd-e--emoji---` | ✅ |
| chemin de 273 caractères | `…-segment-tr-erxhkv` (200 + hash) | ✅ |
| `/tmp/CCTransfert test/lien` → symlink vers `Dossier.été_v2` | slug de la cible | ✅ (realpath) |

Slugs déjà présents sur la machine, tous retrouvés par le calcul : par exemple
`/home/alice/Téléchargements/image` → `-home-alice-T-l-chargements-image`.

### Windows (? Windows)

- `C:\Users\x\Documents de travail\MonProjet` → `C--Users-x-Documents-de-travail-MonProjet`
  selon l'algorithme. La casse de la lettre de lecteur dépend de ce que renvoie
  `realpath` pour le cwd : `doctor` la relève dans les slugs existants.
- Le code compare parfois les slugs sans tenir compte de la casse (option passée
  selon la plateforme) : sous Windows, `C--…` et `c--…` désignent probablement le même
  projet pour le picker. À confirmer.

## 3. Où sont les données d'une session

Pour une session `<sid>` lancée dans `<root>` (slug `<slug>`), `<cfg>` étant le dossier
de config et `<tmp>` la racine temp de Claude Code (§4) :

| Chemin | Contenu |
|---|---|
| `<cfg>/projects/<slug>/<sid>.jsonl` | transcript principal |
| `<cfg>/projects/<slug>/<sid>/subagents/agent-<agentId>.jsonl` | transcript de chaque sous-agent (même `sessionId` que le parent, `isSidechain: true`, `agentId`) |
| `<cfg>/projects/<slug>/<sid>/subagents/agent-<agentId>.meta.json` | `{agentType, description, toolUseId, spawnDepth, …}` |
| `<cfg>/projects/<slug>/<sid>/tool-results/<id>.txt` | grosses sorties d'outils, référencées par `toolUseResult.persistedOutputPath` (chemin **absolu**) |
| `<cfg>/projects/<slug>/<sid>/custom-title.json` | `{"customTitle": "…"}` après `/rename` |
| `<cfg>/projects/<slug>/<sid>/auto-mode-classifier-error.txt`, `scratchpad/` (anciennes versions) | divers : on copie le dossier entier |
| `<cfg>/file-history/<sid>/<hash16>@v<N>` | contenu des fichiers avant modification (rewind) |
| `<cfg>/plans/<slug-plan>.md` (+ `<slug-plan>-agent-<id>.md`) | plan ; `<slug-plan>` est le champ `slug` des lignes (ex. `starry-tumbling-squid`), **pas** le slug du projet. Le réglage `plansDirectory` peut placer les plans dans le projet (relatif à la racine) : ils voyagent alors avec le code |
| `<cfg>/tasks/<listId>/<taskId>.json` | listes de tâches ; `listId` = `CLAUDE_CODE_TASK_LIST_ID`, ou nom d'équipe, ou **l'id de session** (assaini `[^a-zA-Z0-9_-]` → `-`) |
| `<cfg>/todos/<sid>-*.json` | anciennes versions (absent ici) |
| `<cfg>/projects/<slug>/sessions-index.json` | anciennes versions (absent ici, introuvable dans le code 2.1.301) |
| `<cfg>/projects/<slug>/memory/` | auto memory, **niveau projet** |
| `<tmp>/<slug>/<sid>/scratchpad/`, `<tmp>/<slug>/<sid>/tasks/<agentId>.output` | fichiers temporaires (dossier temp système, éphémère) |

**⚠ écart :** les sorties d'agents en arrière-plan (`outputFile`) et le scratchpad sont
dans le dossier **temp**, pas dans `<cfg>/tasks/`.

## 4. Dossier temporaire de Claude Code

```js
tempRoot = join(process.env.CLAUDE_CODE_TMPDIR || os.tmpdir(), `claude-${process.getuid?.() ?? 0}`)
// puis <tempRoot>/<slug>/<sid>/scratchpad, …/tasks
```

- Linux : `/tmp/claude-1000/-home-alice-Projets-MonProjet/<sid>/scratchpad/…`
- **⚠ écart ? Windows** : `process.getuid` n'existe pas sous Windows, donc le code
  donnerait `%TEMP%\claude-0\<slug>\…` et non `%TEMP%\claude\<slug>\…`. `doctor`
  listera les dossiers `claude*` présents dans `%TEMP%` pour trancher.
- `os.tmpdir()` de Node : `$TMPDIR`, `$TMP` ou `$TEMP`, sinon `/tmp` (Linux) ;
  `%TEMP%`, `%TMP%` ou `%SystemRoot%\temp` (Windows).

## 5. Format des transcripts JSONL

- UTF-8, une valeur JSON par ligne, `\n`, JSON **compact** (`JSON.stringify` sans
  espaces), caractères non ASCII écrits tels quels.
- **Une ligne réellement corrompue** a été trouvée : 20 octets NUL avant un objet
  valide (écriture interrompue). Elle doit être recopiée telle quelle.
- Claude Code ré-ajoute en fin de fichier les métadonnées (`last-prompt`,
  `custom-title`, `ai-title`, `tag`, `relocated`, `agent-name`…) et lit la **tête** et la
  **queue** du fichier pour lister les sessions.

### Types de lignes observés (23)

| `type` | Champs notables | Chemins ? |
|---|---|---|
| `user` | `uuid`, `parentUuid`, `sessionId`, `cwd`, `gitBranch`, `version`, `timestamp`, `message`, `toolUseResult`, `promptId`, `isSidechain`, `isMeta`, `slug`, `session_id`… | oui, partout |
| `assistant` | idem + `requestId`, `message.content[]` (`text`, `thinking`, `tool_use`) | oui (inputs d'outils, texte) |
| `attachment` | `attachment.type` parmi 30 sous-types (`environment`, `instructions`, `nested_memory`, `edited_text_file`, `file`, `hook_success`, `prompt_snapshot`, `plan_mode_exit`, `dynamic_skill`…) | oui (`path`, `filename`, `displayPath`, `skillDir`, `planFilePath`, `workingDirectory`…) |
| `system` | `subtype` (`stop_hook_summary`, `turn_duration`, `compact_boundary`, `local_command`…) | oui |
| `file-history-snapshot` | `messageId`, `snapshot.trackedFileBackups{chemin: {...}}`, `isSnapshotUpdate` | **clés de dictionnaire** |
| `file-history-delta` | `messageId`, `snapshotMessageId`, `trackingPath`, `backup{backupFileName, version, backupTime, realParentDir}` | oui |
| `last-prompt` | `lastPrompt`, `leafUuid` | texte libre |
| `ai-title` / `custom-title` / `agent-name` / `tag` | titre généré / `/rename` / nom d'agent | non |
| `relocated` | `relocatedCwd` | **oui** (voir §7) |
| `mode`, `permission-mode`, `atis-latch`, `isolation-latch`, `queue-operation`, `cost-state` | état | `queue-operation.content` = texte libre |
| `pr-link`, `frame-link`, `artifact-comment-monitor`, `artifact-autoreact-ledger` | liens PR / artifacts | `frame-link.path` |
| `api-request`, `api-request-shape`, `api-request-blob` | diagnostic des requêtes API, **adressé par hash** (`hash`, `shapeHash`) | oui, mais voir §6 |

### Chemins : où ils apparaissent

- `cwd` sur chaque ligne `user`/`assistant`/`attachment`/`system`. Il **change** en
  cours de session (sous-dossiers après `cd`). La racine du projet est le `cwd` de la
  première ligne qui en a un, sauf ligne `relocated` en fin de fichier.
- Inputs d'outils : `file_path` (Read/Edit/Write), `notebook_path`, `path`, `command`
  (Bash, texte libre), `root`/`out_dir`/`paths[]` (Artifact), `prompt` (Agent, texte
  libre)…
- `toolUseResult` : `filePath`, `originalFile` (contenu complet du fichier),
  `structuredPatch`, `stdout`/`stderr`, `persistedOutputPath`, `outputFile`,
  `backgroundCwdHint` (phrase contenant un chemin)…
- `file-history-snapshot.snapshot.trackedFileBackups` : **clés = chemins**.
  - **⚠ précision :** clés **relatives au cwd** pour les fichiers du projet (≈ 96 %),
    absolues pour les fichiers hors projet (scratchpad, `~/.claude/...`).
  - `realParentDir` : toujours absolu.
  - `backupFileName = sha256(chemin absolu)[:16] + "@v" + version` (vérifié sur 52 000
    entrées). Le nom est **stocké** : on ne renomme pas les fichiers de
    `file-history/`, le rewind les retrouve par ce nom. Les nouvelles sauvegardes
    créées après l'import utiliseront le hash du nouveau chemin, sans collision.
- `displayPath` : chemins **relatifs** au cwd (`../../../../tmp/claude-1000/...`). Ils
  servent à l'affichage et ne sont pas réécrits.
- Texte libre : prompts, réponses, sorties de commandes, `lastPrompt`,
  `prompt_snapshot.systemPrompt`…

### Contenus à ne jamais toucher

- Blocs `thinking` (`{type, thinking, signature}`) dans `assistant` (6 468 vus). Aucun
  `redacted_thinking` vu, mais traité de même.
- Images base64 : `{"type":"image","source":{"type":"base64","data":…}}` dans les
  prompts et dans les `tool_result`, et `toolUseResult.file.base64`.
- Identifiants : `uuid`, `parentUuid`, `leafUuid`, `logicalParentUuid`, `messageId`,
  `promptId`, `requestId`, `message.id`, `tool_use_id`, `toolUseID`,
  `sourceToolAssistantUUID`…

### `sessionId` et `session_id`

- `sessionId` (camelCase) est présent sur presque toutes les lignes, sous-agents
  compris (même valeur que le parent).
- **⚠ nouveau :** un champ `session_id` (snake_case) apparaît sur certaines lignes avec
  **d'autres** identifiants (sessions précédentes reprises ou forkées). Avec `--new-id`,
  on ne remplace que les occurrences de l'ancien `sessionId`.

## 6. Lignes de diagnostic adressées par hash

`api-request-blob` (`hash` + `message`), `api-request-shape` (`shapeHash` + `shape`) et
`api-request` (`shapeHash`, `params`) stockent une copie des requêtes envoyées à l'API.
Le `hash` n'est pas un simple sha256 du JSON compact : il serait invalidé par une
réécriture. **Décision : recopier ces lignes à l'identique** (seul `sessionId` est
changé avec `--new-id`). Elles ne sont pas renvoyées au modèle à la reprise.

## 7. Mécanismes internes utiles

- **Doublons d'ID (confirmé dans le code)** : `claude --resume <id>` scanne
  `projects/*/<id>.jsonl`. S'il en trouve deux, il renvoie « introuvable ».
- **Relocalisation native (⚠ nouveau)** : Claude Code 2.1.301 sait déplacer un
  transcript d'un dossier projet à un autre (`relocateSessionTranscript`). Il écrit
  alors une ligne `{"type":"relocated","sessionId":…,"relocatedCwd":…}`. Pour rattacher
  un transcript à un dossier, il lit en priorité le `relocatedCwd` de la **fin** du
  fichier, sinon le `cwd` du **début**. Notre import réécrit ces deux champs.
- **Session en cours** : `sessions/<pid>.json` contient `pid`, `sessionId`, `cwd`,
  `status` (`busy`/`idle`), `procStart`, `version`, `entrypoint`, `name`. Le fichier
  peut survivre au processus. Sous Linux, `procStart` est égal au champ 22 de
  `/proc/<pid>/stat` (heure de démarrage) : la comparaison évite les faux positifs dus
  à la réutilisation de PID (vérifié sur 3 sessions vivantes).
- **Nettoyage** : réglage `cleanupPeriodDays` (30 jours par défaut selon la doc), date
  du dernier passage dans `<cfg>/.last-cleanup`. Le nettoyage est suspendu si les
  settings sont invalides.
- **Titre d'une session** : dernier `custom-title` (`/rename`), sinon dernier
  `ai-title`, sinon premier prompt utilisateur. `sessions/<pid>.json` a aussi `name`.
- **Le picker `claude -r` masque certaines sessions (⚠ nouveau, vérifié)** : celles
  dont l'`entrypoint` est `sdk-cli`, `sdk-ts` ou `sdk-py` (créées par `claude -p` ou
  l'Agent SDK), et celles de type `daemon`/`daemon-worker`. Elles restent reprenables
  avec `claude --resume <id>`. `CCTransfert list` les marque `[-p]`.
- **Picker** : la date affichée vient du dernier message, pas du mtime du fichier.
- **`session-env/<sid>/`** : scripts écrits par les hooks `SessionStart` via
  `$CLAUDE_ENV_FILE`, exécutés avant chaque commande Bash, sans confirmation. Claude Code
  interdit d'y écrire (risque d'exécution de code). Ils ne sont pas transférés : ils
  seraient propres à la machine source, et les hooks de la cible se relancent à la
  reprise.

## 7 bis. Vérifications de bout en bout (2026-10-16, Linux, 2.1.301)

1. Session réelle `claude-vscode` (MonProjet, 396 messages) exportée puis importée avec
   `--new-id` dans un autre dossier : elle apparaît dans `claude -r`.
2. Reprise : interrogé sur les fichiers de la session, Claude répond avec les chemins
   du **nouveau** dossier (`…/Travail/Projets/App/MonProjet/monprojet-frontend/…`).
3. Rewind : session de test (création puis modification de `notes.txt`) transférée,
   puis Échap Échap, message choisi, « Restore code » : le fichier est revenu à sa
   version précédente grâce au `file-history` importé.
4. Aller-retour Linux → Windows (simulé) → Linux sur les 82 transcripts de la machine
   (75 723 lignes) : aucune erreur, blocs thinking intacts, octets identiques sauf
   6 lignes qui contenaient déjà le chemin Windows cible en clair.
5. Au premier lancement dans le dossier cible, Claude Code demande bien de faire
   confiance au dossier (entrée ajoutée dans `~/.claude.json` par Claude Code
   lui-même).

## 8. Ce que `claude -p` crée

Une session `-p` crée seulement :

- `projects/<slug>/<sid>.jsonl` ;
- un dossier vide `projects/<slug>/memory/` s'il n'existe pas ;
- un dossier vide `session-env/<sid>/`.

Rien dans `history.jsonl` ni dans `~/.claude.json`. Utile pour `--calibrate` : on sait
exactement quoi supprimer.

## 9. Choix d'implémentation qui en découlent

- `history.jsonl` : `{display, pastedContents, timestamp, project, sessionId}`. Avec
  `export --with-history`, les entrées de la session voyagent ; à l'import, `project`
  est réécrit, les doublons exacts sont ignorés et `undo` retire les lignes ajoutées.
  Les sessions `claude -p` n'écrivent rien dans `history.jsonl`.
- Scratchpad (`<tmp>/<slug>/<sid>/scratchpad/`) : éphémère, souvent déjà nettoyé. Les
  chemins sont réécrits mais les fichiers ne sont pas transférés.
- Formes de chemins Windows reconnues dans les données : `C:\…`, `C:/…`, `c:\…`,
  `C:\\…` (échappée, ex. JSON affiché par une commande) et `/c/…` (Git Bash).
