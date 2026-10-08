# Politique de dépendances Python — OP-016

## Objectif

Garantir qu'un même état du dépôt installe les mêmes versions de bibliothèques sur les postes de développement, en intégration continue et lors d'un déploiement.

## Règles

- `requirements.txt` constitue le verrou exécutable et toutes ses lignes actives utilisent `nom==version`.
- Le verrou de versions garantit la reproductibilité, **pas à lui seul
  l'authenticité** des paquets. Avant tout nouvel ajout ou changement de
  version, vérifier la provenance officielle, l'éditeur/mainteneur, la somme
  SHA-256 de l'artefact obtenu, les avis de sécurité connus et la licence.
  Ne jamais accepter silencieusement un paquet provenant d'une URL, d'un
  dépôt ou d'un miroir non approuvé. Si une vérification manque, bloquer
  l'installation et documenter la limite avant de demander une décision.
- Le fichier de verrou est normalisé en UTF-8 afin d'être lu de façon identique par Windows, Linux, Python et la CI.
- Les dépendances critiques sont contrôlées automatiquement avant et après installation.
- La CI utilise Python 3.12. Les postes Python 3.11 restent acceptés pendant la transition tant que la suite complète réussit.
- Le correctif de sécurité d'une version Python peut évoluer sans modifier le code applicatif ; sa version exacte doit apparaître dans les journaux de build.
- Une mise à jour de dépendance est réalisée dans une branche dédiée, jamais silencieusement.
- Une mise à jour doit inclure : justification, contrôle du verrou, installation dans un environnement neuf, suite complète de tests et essai de démarrage.
- L'audit d'authenticité des dépendances déjà présentes reste à réaliser :
  cette règle ne transforme pas rétroactivement leur verrou en preuve de
  provenance ou de sûreté.
- Les montées de version majeures sont traitées séparément des changements fonctionnels.

## Procédure de mise à jour

1. Modifier explicitement les versions concernées dans `requirements.txt`.
2. Exécuter `tools/check_dependency_lock.py`.
3. Installer le fichier dans un environnement Python neuf.
4. Exécuter toute la suite de tests backend.
5. Vérifier l'import de `app.main` et le démarrage sur un port de test disponible.
6. Conserver la preuve de validation dans `reports/op016/` ou dans les artefacts CI.

## Compromis retenu

Le verrou porte sur toutes les bibliothèques, directes et transitives, pour une reproductibilité maximale. La version mineure Python de référence est fixée en CI, mais le correctif de sécurité n'est pas bloqué dans le dépôt : cela évite de retarder les correctifs Python tout en conservant une trace exacte dans chaque build.
