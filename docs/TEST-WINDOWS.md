# Checklist de test sur le PC Windows

Objectif : valider sur un vrai Windows ce qui n'a pu être testé que par simulation
depuis Linux :
- emplacement du dossier temp ;
- casse de la lettre de lecteur ;
- slugs ;
- reprise et rewind après transfert, dans les deux sens.

Cochez au fur et à mesure et notez tout écart. Les commandes sont données pour
PowerShell.

---

## 0. Préparation

Le plus simple est la **version autonome**, qui ne demande pas Python :

- [ ] Sur la page des releases, télécharger `CCTransfert-<version>-windows-x64.exe` et
      `cctransfert-cli-<version>-windows-x64.exe`, par exemple dans `C:\Outils`.
- [ ] Double-clic sur `CCTransfert-….exe`. Si SmartScreen affiche « Windows a protégé
      votre ordinateur », cliquer sur **Informations complémentaires**, puis
      **Exécuter quand même**. Noter si un antivirus le bloque.
- [ ] L'interface s'ouvre (fenêtre Edge sans barre d'adresse), **sans fenêtre de console**.
- [ ] Claude Code est installé **sous Windows** (`claude --version`). S'il est dans WSL,
      notez-le : il faudra utiliser CCTransfert dans WSL.
- [ ] Dans la suite, `CCTransfert` désigne `C:\Outils\cctransfert-cli-<version>-windows-x64.exe`
      dans PowerShell, et `CCTransfert --version` affiche la bonne version.

Variante avec Python (python.org, 3.9+) : récupérer le dépôt, puis utiliser
`bin\CCTransfert.cmd` ou double-cliquer sur `CCTransfert.pyw`.

## 1. Diagnostic (le plus important)

```powershell
cd "C:\Users\alice\Documents de travail\Projets\MonProjet"   # un dossier où Claude Code a déjà servi
CCTransfert doctor
CCTransfert doctor --json > "$env:USERPROFILE\Desktop\doctor-windows.json"
```

Notez :
- [ ] **Config Claude** : `C:\Users\alice\.claude` attendu, sinon noter la valeur.
- [ ] **Slugs vérifiés** : « N conformes au calcul, 0 différents ». S'il y a des
      différences, recopiez les lignes ✗.
- [ ] **Lettre lecteur** : combien en majuscule (`C--`), combien en minuscule (`c--`) ?
- [ ] **Temp de Claude** et la ligne « dossiers claude* dans … » : est-ce `claude-0`,
      `claude`, ou autre chose ?
- [ ] **slug calculé** du dossier courant : « dossier existant » ?
- [ ] Les éventuels avertissements ⚠ (WSL, `claude` introuvable…).

Vérification manuelle complémentaire :

```powershell
dir "$env:USERPROFILE\.claude\projects"     # noms des dossiers projet
dir $env:TEMP | findstr /i claude           # dossier temp de Claude Code
```

- [ ] Les noms des dossiers projet commencent-ils par `C--` ou par `c--` ?

**Envoyez `doctor-windows.json`** : il permet de corriger l'outil si besoin.

## 2. Linux → Windows

Sur le PC Linux :

```sh
cd /home/alice/Projets/MonProjet
CCTransfert list
CCTransfert export <N°> -o ~/Transferts/ --with-memory --with-history
```

Choisissez une session **terminée** (`/exit`), interactive (pas `[-p]`), qui a modifié
des fichiers. Copiez le `.CCTransfert` sur le PC Windows.

Sur le PC Windows :

- [ ] `CCTransfert inspect <fichier>` : résumé correct, « sha256 vérifiés ✓ ».
- [ ] Le code du projet est à jour dans `C:\Users\alice\Documents de travail\Projets\MonProjet`
      (git pull, même branche que dans le résumé).
- [ ] Dry-run :
      ```powershell
      CCTransfert import <fichier> --to "C:\Users\alice\Documents de travail\Projets\MonProjet" --dry-run
      ```
      Vérifiez dans la sortie :
      - « Dossier Claude : …\projects\C--Users-alice-Documents-de-travail-Projets-MonProjet »
        (ou `c--…` selon la convention relevée au §1) ;
      - les exemples avant/après : chemins en `C:\Users\alice\…` dans les champs,
        `C:/Users/…` dans les commandes ;
      - la liste « Chemins absolus restés tels quels » : notez ceux qui auraient dû être
        convertis.
- [ ] Import réel : même commande sans `--dry-run`. Pas d'erreur, puis « ✓ Session importée ».
- [ ] `cd "C:\Users\alice\Documents de travail\Projets\MonProjet"; claude -r` :
      - [ ] la demande de confiance du dossier apparaît (normal la première fois) ;
      - [ ] **la session apparaît dans la liste**, avec son titre.
- [ ] Reprendre la session et demander : « Sur quels fichiers est-ce qu'on travaillait ?
      Donne les chemins complets. »
      - [ ] Les chemins cités sont des chemins **Windows** du bon dossier.
- [ ] Demander une petite action réelle (« lis le fichier X et résume-le ») : l'outil
      Read trouve le fichier.
- [ ] `claude --resume <id>` depuis le dossier fonctionne aussi.
- [ ] Ouvrir le dossier `…\.claude\projects\<slug>\<id>\` : `subagents\` et
      `tool-results\` présents si la session en avait.

## 3. Rewind après transfert

Avec une session de test pour ne rien risquer, sur Linux :

```sh
mkdir -p ~/tmp/cct-rewind && cd ~/tmp/cct-rewind
claude      # demander : « crée notes.txt contenant version 1 », puis « remplace par version 2 », puis /exit
CCTransfert export --latest -o ~/Transferts/
```

Sur Windows :

- [ ] Créer `C:\Temp\cct-rewind`, y copier `notes.txt` (contenu `version 2`).
- [ ] `CCTransfert import <fichier> --to C:\Temp\cct-rewind`
- [ ] `cd C:\Temp\cct-rewind; claude -r`, reprendre la session, puis **Échap Échap**.
- [ ] Les messages affichent `notes.txt +…` : le file-history est reconnu.
- [ ] Choisir le message « remplace par version 2 », puis **Restore code**.
- [ ] `type notes.txt` affiche `version 1`.

## 4. Windows → Linux

Sur Windows, dans un dossier **avec espaces et accents**, par exemple
`C:\Users\alice\Documents de travail\Test été` :

- [ ] Lancer `claude`, faire créer et modifier un fichier, lancer une commande Bash
      (`ls`), puis `/exit`.
- [ ] `CCTransfert list` : la session apparaît avec le bon dossier.
- [ ] `CCTransfert export --latest -o C:\Temp\`

Sur Linux :

- [ ] `CCTransfert import <fichier> --to "/home/alice/Projets/Test été" --dry-run` :
      exemples avant/après en `/home/alice/…`. Les formes `/c/Users/…` des
      commandes Bash sont aussi converties.
- [ ] Import réel, puis `claude -r` dans le dossier : la session apparaît et se reprend.

## 5. Aller-retour A → B → A

- [ ] Sur Windows, reprendre la session importée au §2, échanger un message, `/exit`.
- [ ] `CCTransfert export <id> -o C:\Temp\`, puis copier le fichier vers Linux.
- [ ] Sur Linux : `CCTransfert import <fichier> --to /home/alice/Projets/MonProjet`
      - [ ] l'outil signale que la session existe déjà ici et propose de la remplacer
            (ou utilisez `--replace`) ;
      - [ ] après import, `claude -r` montre la session avec le message échangé sous Windows.
- [ ] `CCTransfert undo` restaure la version d'avant.

## 6. Interface graphique

- [ ] Dans l'Explorateur, **double-clic sur `CCTransfert.pyw`** (racine du dépôt) : l'interface
      s'ouvre, sans fenêtre de console.
- [ ] Glisser un fichier `.CCTransfert` sur `CCTransfert.pyw` : il s'ouvre dans l'onglet Importer.
- [ ] Si rien ne s'ouvre : clic droit → « Ouvrir avec » → « Python » (ou `pythonw`), et
      noter ce qui est associé aux `.pyw` ; regarder `%USERPROFILE%\.CCTransfert\gui.log`.
- [ ] `CCTransfert gui` ouvre une fenêtre Edge (ou Chrome) sans barre d'adresse.
- [ ] Onglet **Exporter** : les sessions Windows apparaissent, regroupées par projet ;
      l'export crée le fichier dans `Téléchargements` ; « Ouvrir le dossier » ouvre
      l'Explorateur sur le fichier.
- [ ] Onglet **Importer** : glisser-déposer un `.CCTransfert` depuis l'Explorateur
      fonctionne ; « Parcourir… » liste les lecteurs (`C:\`, `D:\`…).
- [ ] Aperçu puis import d'un bundle venant de Linux : mêmes vérifications qu'au §2.
- [ ] Fermer la fenêtre arrête bien `CCTransfert` : plus de processus `python` dans le
      Gestionnaire des tâches après quelques secondes.
- [ ] `CCTransfert gui --install-shortcut` crée « CCTransfert » dans le menu Démarrer
      **et** sur le Bureau, avec l'icône CCTransfert (carré bleu, deux documents, flèche).
- [ ] Un double-clic sur l'icône du Bureau ouvre l'interface **sans fenêtre de console**.
- [ ] Un deuxième double-clic ouvre une deuxième fenêtre, sans lancer un deuxième
      `pythonw.exe` (vérifier dans le Gestionnaire des tâches).
- [ ] `CCTransfert gui --remove-shortcut` retire les deux raccourcis.

## 6 bis. Version autonome : raccourcis, duplication, mise à jour

- [ ] Onglet **Réglages** : cocher « Icône sur le bureau » puis « Raccourci dans le menu
      Démarrer ». Vérifier que :
      - `CCTransfert.exe` a été copié dans `%LOCALAPPDATA%\Programs\CCTransfert` ;
      - les deux raccourcis ont l'icône CCTransfert ;
      - ils ouvrent l'interface même après suppression de l'exe téléchargé.
- [ ] Décocher les deux cases : les raccourcis disparaissent.
- [ ] Onglet **Exporter** → une session → **Dupliquer…** → même dossier, intitulé « essai
      copie ». Vérifier que :
      - la copie apparaît dans la liste ;
      - `claude -r` dans le dossier montre les deux sessions ;
      - la copie se reprend ;
      - **Historique** → « Annuler le dernier import » la supprime.
- [ ] **Réglages** → « Vérifier maintenant » affiche la dernière version, ou « à jour ».
- [ ] Mise à jour réelle, quand une version plus récente existe : lancer l'ancienne
      version installée. Vérifier que :
      - le bandeau « Une nouvelle version… » apparaît ;
      - « Mettre à jour » télécharge la nouvelle version, ferme la fenêtre et en rouvre une ;
      - le numéro affiché en haut à gauche est le nouveau ;
      - il ne reste pas de `CCTransfert.exe.old` au lancement suivant.

## 7. Cas particuliers (si le temps le permet)

- [ ] Projet sur un autre lecteur (`D:\Projets\X`) ou un lecteur réseau / `subst`.
      Noter le slug créé par Claude Code (`dir ~\.claude\projects`) et celui de
      `CCTransfert doctor` lancé dans ce dossier.
- [ ] Chemin très long (plus de 200 caractères) : `CCTransfert import … --calibrate`
      affiche « Slug observé » ; comparer avec le calcul.
- [ ] Session en cours : lancer `claude` dans le dossier cible, puis tenter l'import
      dans un autre terminal. Résultat attendu : refus « en cours ».
- [ ] `CCTransfert export … --encrypt` si `gpg` ou `age` est installé, puis import.

## À renvoyer

- `doctor-windows.json` (§1) ;
- les cases non cochées, avec le message d'erreur complet ;
- la section « Chemins absolus restés tels quels » des imports (§2 et §4) ;
- une capture du picker `claude -r` (§2) si la session n'y apparaît pas.
