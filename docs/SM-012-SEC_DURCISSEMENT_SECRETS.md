# SM-012-SEC - Durcissement de la gestion des secrets

## Objectif

Empêcher qu'un mot de passe, un jeton ou une clé soit exposé dans Git, dans
les traces série ou dans une réponse MQTT, et réduire la durée de vie des
copies temporaires en RAM.

## Protections ajoutées

- redaction récursive des champs sensibles avant sérialisation des réponses ;
- suppression du journal complet des réponses MQTT ;
- effacement explicite des buffers de commandes et de réponses ;
- effacement explicite des mots de passe temporaires du provisioning BLE/Wi-Fi ;
- effacement des copies temporaires utilisées lors des lectures/écritures NVS ;
- modèle `.env.example` sans secret pour le backend ;
- contrôles automatiques dans les CI firmware et backend ;
- autotest embarqué de la redaction au démarrage.

## Choix et compromis

Le firmware conserve nécessairement les identifiants actifs dans sa
configuration RAM pour se reconnecter au Wi-Fi et au broker. L'effacement
cible donc les copies temporaires, sans casser les reconnexions.

Les secrets NVS restent stockés selon le fonctionnement actuel du framework.
Cette étape ne programme aucune eFuse et n'active ni Secure Boot, ni Flash
Encryption, ni NVS Encryption. Ces opérations sont liées au procédé industriel
et doivent être validées sur des cartes dédiées avant la production, car elles
modifient les possibilités de flash et de récupération.

## Critères de validation

1. Les contrôles SM-012-SEC firmware et backend passent.
2. Les builds DEV et PROD passent.
3. La suite de tests backend passe.
4. Le moniteur série affiche l'autotest PASS sans contenu sensible.
5. Wi-Fi, MQTT, application et commandes autorisées restent fonctionnels.
