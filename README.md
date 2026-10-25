# DoIP-UDS-Lab

Banc de laboratoire Python pour expérimenter le diagnostic automobile moderne avec **UDS** (*Unified Diagnostic Services*, ISO 14229) transporté par **DoIP** (*Diagnostic over Internet Protocol*, ISO 13400-2).

Le projet fournit une base pour développer un client de diagnostic asynchrone, un simulateur de Gateway et d’ECU, ainsi que des outils d’analyse des codes défauts, des réponses négatives et des mécanismes de sécurité.

> **Avertissement** : ce projet est destiné à l’apprentissage, au développement sur banc de test et au diagnostic de véhicules dont vous êtes propriétaire ou pour lesquels vous disposez d’une autorisation explicite. Ne l’utilisez pas sur un véhicule en circulation ou sur un système dont vous n’avez pas le droit de modifier le comportement.

## Objectifs

Le projet vise à rendre concrète une pile de diagnostic automobile complète, sans dépendre immédiatement d’un véhicule physique.

Il permet notamment de :

- Comprendre la séparation entre les couches TCP/IP, DoIP, ISO-TP et UDS.
- Développer un client DoIP asynchrone en Python avec `asyncio`.
- Simuler une Gateway DoIP et un calculateur UDS sur un poste local.
- Tester la lecture des DTC, le *freeze frame* et les données étendues.
- Étudier les réponses négatives UDS, notamment les NRC `0x78` et `0x73`.
- Expérimenter les mécanismes de sécurité `0x27 SecurityAccess` et `0x29 Authentication`.
- Préparer des tests automatisés avant toute connexion à un véhicule réel.

## Architecture logique

Le projet repose sur une séparation stricte des responsabilités.

```text
+----------------+     TCP/IP      +------------------+     Bus interne     +-----------+
| Client DoIP    | --------------> | Gateway DoIP     | ------------------> | ECU UDS   |
| Testeur        | <-------------- | ISO 13400-2      | <------------------ | ISO 14229 |
+----------------+                 +------------------+                     +-----------+
```

Dans un véhicule réel, la Gateway DoIP reçoit les messages du testeur sur Ethernet, puis les route vers un calculateur interne via CAN, CAN-FD, FlexRay ou un autre réseau embarqué.

Dans ce laboratoire, la Gateway et l’ECU sont simulés par un même processus Python. Cela permet de valider le client sans matériel automobile.

## Composants prévus

### Client DoIP

Le client est responsable des opérations suivantes :

- Découverte du véhicule par UDP.
- Ouverture d’une connexion TCP vers la Gateway.
- Activation du routage diagnostique.
- Encapsulation des requêtes UDS dans des messages DoIP.
- Réception et démultiplexage des réponses UDS.
- Gestion des ACK et NACK DoIP.
- Réponse automatique aux requêtes Alive Check.
- Gestion des temporisations UDS `P2` et `P2*`.

### Simulateur de véhicule

Le simulateur joue le rôle d’une Gateway et d’un ou plusieurs ECU.

Il peut répondre à des requêtes telles que :

- `0x10 DiagnosticSessionControl`.
- `0x19 ReadDTCInformation`.
- `0x14 ClearDiagnosticInformation`.
- `0x22 ReadDataByIdentifier`.
- `0x27 SecurityAccess`.
- `0x29 Authentication`.
- `0x31 RoutineControl`.
- `0x34`, `0x36` et `0x37` pour les transferts de données.

### Outils d’analyse

Le projet peut également inclure des utilitaires pour :

- Décoder un DTC sur trois octets.
- Interpréter le `DTCStatusAvailabilityMask`.
- Décoder les huit bits du statut d’un DTC.
- Analyser les trames DoIP brutes.
- Générer des rapports de diagnostic.

## Notions UDS essentielles

### Requête et réponse

Une requête UDS commence par un identifiant de service, appelé SID.

```text
Requête  : 19 02 08
Réponse  : 59 02 37 C1 23 87 09
```

Dans cet exemple :

- `0x19` est le service `ReadDTCInformation`.
- `0x02` est la sous-fonction `reportDTCByStatusMask`.
- `0x08` filtre les DTC confirmés.
- `0x59` est la réponse positive : `0x19 + 0x40`.
- `0x37` est le `DTCStatusAvailabilityMask`.
- `C1 23 87` est un DTC.
- `09` est le statut du DTC.

### Réponse négative

Si l’ECU refuse une requête, il renvoie une réponse négative.

```text
7F 22 33
```

Cette trame signifie :

- `0x7F` : réponse négative UDS.
- `0x22` : service `ReadDataByIdentifier` concerné.
- `0x33` : NRC `securityAccessDenied`.

Le NRC `0x33` indique que le service est connu, mais que la sécurité doit être déverrouillée au préalable.

### NRC 0x78

Le NRC `0x78`, nommé `requestCorrectlyReceived-ResponsePending`, indique que l’ECU a accepté la requête mais n’est pas encore prêt à fournir la réponse finale.

```text
7F 31 78
```

Le client ne doit pas considérer cette réponse comme un échec. Il doit réarmer son temporisateur `P2*` et continuer à attendre la réponse définitive.

### NRC 0x73

Le NRC `0x73`, nommé `wrongBlockSequenceCounter`, concerne principalement le service `0x36 TransferData`.

```text
7F 36 73
```

Il signale que le compteur de bloc envoyé par le testeur ne correspond pas au compteur attendu par l’ECU. Cette erreur est généralement fatale pour la séquence de transfert en cours.

## Structure du DTC

Un DTC UDS est généralement représenté sur trois octets.

```text
DTC_High DTC_Middle DTC_Low
```

À ces trois octets s’ajoute souvent un octet de statut.

```text
C1 23 87 09
```

Ici :

- `C1 23 87` identifie le défaut.
- `09` est le statut binaire `0000 1001`.

Le statut `0x09` signifie :

- `testFailed` actif.
- `confirmedDTC` actif.

Un DTC peut donc être confirmé et mémorisé sans être encore présent au moment de la lecture.

## Décodage du statut DTC

Le statut DTC est un masque de huit bits.

| Bit | Masque | Nom | Signification |
|---:|---:|---|---|
| 0 | `0x01` | `testFailed` | Le dernier test exécuté a échoué. |
| 1 | `0x02` | `testFailedThisOperationCycle` | Au moins un échec durant le cycle courant. |
| 2 | `0x04` | `pendingDTC` | Défaut détecté mais non confirmé. |
| 3 | `0x08` | `confirmedDTC` | Défaut confirmé et mémorisé. |
| 4 | `0x10` | `testNotCompletedSinceLastClear` | Test non exécuté depuis le dernier effacement. |
| 5 | `0x20` | `testFailedSinceLastClear` | Au moins un échec depuis le dernier effacement. |
| 6 | `0x40` | `testNotCompletedThisOperationCycle` | Test non exécuté durant le cycle courant. |
| 7 | `0x80` | `warningIndicatorRequested` | Demande d’allumage du voyant MIL. |

### Exemple Python

```python
def decode_dtc_status(status: int) -> dict[str, bool]:
    return {
        "testFailed": bool(status & 0x01),
        "testFailedThisOperationCycle": bool(status & 0x02),
        "pendingDTC": bool(status & 0x04),
        "confirmedDTC": bool(status & 0x08),
        "testNotCompletedSinceLastClear": bool(status & 0x10),
        "testFailedSinceLastClear": bool(status & 0x20),
        "testNotCompletedThisOperationCycle": bool(status & 0x40),
        "warningIndicatorRequested": bool(status & 0x80),
    }


print(decode_dtc_status(0x09))
```

## Différence entre DTC, freeze frame et extended data

Ces trois notions sont complémentaires.

| Donnée | Service UDS | Question à laquelle elle répond |
|---|---|---|
| DTC et statut | `0x19 02 <mask>` | Quel défaut est présent, confirmé ou historique ? |
| Freeze frame | `0x19 04 <DTC> <record>` | Dans quelles conditions la panne est-elle apparue ? |
| Extended data | `0x19 06 <DTC> <record>` | Combien de fois, depuis quand et avec quel historique ? |

Le *freeze frame* capture un instantané des paramètres du véhicule au moment de la détection : régime moteur, vitesse, température, tension, position papillon, etc.

Les *extended data* peuvent contenir des compteurs d’occurrence, un compteur de vieillissement, des horodatages et d’autres informations définies par le constructeur.

## DoIP en bref

DoIP remplace le transport CAN/ISO-TP entre le testeur et la Gateway par une pile Ethernet/IP.

| Élément | Rôle |
|---|---|
| UDP 13400 | Découverte du véhicule |
| TCP 13400 | Session de diagnostic DoIP |
| `0x0001` | Requête d’identification du véhicule |
| `0x0004` | Annonce ou réponse d’identification |
| `0x0005` | Requête d’activation du routage |
| `0x0006` | Réponse d’activation du routage |
| `0x0007` | Requête Alive Check |
| `0x0008` | Réponse Alive Check |
| `0x8001` | Message de diagnostic contenant une trame UDS |
| `0x8002` | Accusé de réception positif DoIP |
| `0x8003` | Accusé de réception négatif DoIP |

Un ACK DoIP `0x8002` signifie que la Gateway a accepté et routé la requête. Il ne signifie pas que l’ECU a déjà traité la requête UDS.

Un NACK DoIP `0x8003` indique un problème de transport ou de routage. Il est différent d’un NRC UDS, qui signifie que l’ECU a reçu la requête mais l’a refusée au niveau applicatif.

## Installation sous Termux

Mettez à jour les paquets et installez Python.

```bash
pkg update && pkg upgrade
pkg install python git
```

Créez et activez un environnement virtuel.

```bash
cd ~/Projets
python -m venv .venv
source .venv/bin/activate
```

Installez les dépendances de développement.

```bash
pip install --upgrade pip
pip install pytest pytest-asyncio
```

## Arborescence cible

```text
doip-uds-lab/
├── README.md
├── LICENSE
├── pyproject.toml
├── .gitignore
├── src/
│   └── doip_uds_lab/
│       ├── __init__.py
│       ├── client/
│       │   ├── __init__.py
│       │   ├── doip_client.py
│       │   └── uds_client.py
│       ├── server/
│       │   ├── __init__.py
│       │   ├── mock_gateway.py
│       │   └── mock_ecu.py
│       ├── protocol/
│       │   ├── __init__.py
│       │   ├── doip.py
│       │   ├── uds.py
│       │   └── iso_tp.py
│       └── tools/
│           ├── __init__.py
│           ├── dtc_decoder.py
│           └── nrc_decoder.py
└── tests/
    ├── __init__.py
    ├── test_doip_framing.py
    ├── test_uds_responses.py
    └── test_dtc_status.py
```

## Utilisation prévue

### Démarrer le simulateur

```bash
python -m doip_uds_lab.server.mock_gateway
```

### Lancer un client de test

```python
import asyncio

from doip_uds_lab.client.doip_client import DoIPClient


async def main() -> None:
    client = DoIPClient(
        tester_address=0x0E80,
        gateway_ip="127.0.0.1",
    )

    await client.connect()

    response = await client.send_uds(
        target_address=0x2001,
        uds_request=bytes.fromhex("19 02 08"),
    )

    print(response.hex(" ").upper())

    await client.close()


asyncio.run(main())
```

### Exemple de scénario de test

```text
1. Démarrer le mock DoIP.
2. Connecter le client.
3. Activer le routage.
4. Lire les DTC confirmés avec 19 02 08.
5. Déclencher une réponse différée avec 31 01 FF 00.
6. Vérifier que le client gère le NRC 0x78.
7. Envoyer un mauvais compteur 0x36 pour obtenir un NRC 0x73.
8. Fermer proprement la session.
```

## Feuille de route

- [ ] Structurer le paquet Python avec `pyproject.toml`.
- [ ] Implémenter l’encodage et le décodage des en-têtes DoIP.
- [ ] Implémenter le client DoIP asynchrone.
- [ ] Implémenter le mock Gateway et ECU.
- [ ] Ajouter un décodeur de statut DTC.
- [ ] Ajouter un décodeur de NRC.
- [ ] Ajouter les tests unitaires.
- [ ] Ajouter la gestion ISO-TP multi-trames.
- [ ] Ajouter la simulation `SecurityAccess` avec seed/key.
- [ ] Ajouter la simulation `Authentication` avec certificats.
- [ ] Ajouter une documentation des trames capturées.

## Sécurité et éthique

Le diagnostic automobile peut modifier l’état d’un véhicule, effacer des défauts, désactiver des communications ou reprogrammer un calculateur.

Respectez toujours les règles suivantes :

- N’intervenez que sur un véhicule vous appartenant ou avec une autorisation écrite.
- N’utilisez pas ce projet pour contourner des protections, falsifier un compteur kilométrique ou désactiver des fonctions de sécurité.
- Testez d’abord sur un simulateur ou un banc hors véhicule.
- Sauvegardez les données avant toute opération d’écriture ou de reprogrammation.
- Ne diffusez pas d’algorithme seed/key, de certificat ou de clé privée appartenant à un constructeur.

## Licence

Choisissez une licence selon l’usage prévu :

- `MIT` pour un projet simple, permissif et réutilisable.
- `Apache-2.0` si vous souhaitez une clause explicite de brevets.
- `GPL-3.0` si vous souhaitez imposer la même liberté aux œuvres dérivées.

Pour un projet de laboratoire personnel, `MIT` est généralement le choix le plus simple.
