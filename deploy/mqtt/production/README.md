# Politique MQTT de production

Ces fichiers décrivent les autorisations minimales à importer dans un broker
de production distinct du laboratoire.

## Invariants

- une identité backend dédiée ne publie que les commandes et ne souscrit
  qu'aux messages produits par les appareils ;
- chaque appareil possède un identifiant et un secret propres ;
- le Client ID et le nom d'utilisateur de l'appareil sont son `device_uid` ;
- les topics de chaque appareil sont liés explicitement à son
  `mqtt_device_id` ;
- aucune règle appareil avec `smartmonitor/+/...` n'est admise ;
- une règle globale `#`, publication et souscription, `Deny`, termine la
  politique sur EMQX Serverless ;
- le port non chiffré n'est pas utilisé.

`device-acl.template.csv` doit être généré pour chaque appareil après
remplacement des deux marqueurs. Les mots de passe ne figurent jamais dans ces
CSV.

## Rotation sans coupure

1. créer une nouvelle identité portant un nouvel identifiant de rotation ;
2. ajouter ses ACL avant de distribuer le nouveau secret ;
3. basculer le backend ou les appareils concernés ;
4. vérifier connexions, publications et refus négatifs ;
5. révoquer l'ancienne identité ;
6. archiver uniquement la date, l'identifiant de rotation et les preuves de
   test, jamais le secret.

La règle globale de refus est créée dans l'onglet `All Users` de la console et
doit rester la dernière règle. Les fichiers CSV ne la créent pas.
