---
title: Ballon d'eau chaude Tempo : programmer pour économiser
description: Ballon d'eau chaude Tempo : programmez la chauffe en heures creuses, coupez les jours rouges et réduisez la facture. Réglages, contacteur HC et estimations.
publish_date: 2026-07-21
keywords: ballon d'eau chaude tempo, chauffe-eau tempo edf, contacteur heures creuses tempo, programmer chauffe-eau tempo
cluster: equipements
---
Le chauffe-eau électrique est l'un des plus gros postes silencieux de votre facture : il représente environ **15 % de la consommation** d'un foyer, et il fonctionne souvent sans que vous y pensiez. Avec l'offre Tempo EDF, ce détail devient stratégique. Bien réglé, un **ballon d'eau chaude Tempo** chauffe au tarif le plus bas de la grille (les heures creuses, à 0,1575 euros/kWh même un jour rouge) et reste éteint pendant les heures pleines rouges, facturées 0,7060 euros/kWh, soit plus de quatre fois plus cher. Mal réglé, il fait exactement l'inverse.

L'objectif de cet article est simple : vous donner les réglages concrets pour que votre chauffe-eau ne chauffe jamais au mauvais moment. Nous verrons comment utiliser le contacteur heures creuses, caler la chauffe sur 22h-6h, couper le ballon les jours rouges en ayant chauffé la veille, et quelques ordres de grandeur d'économie. Pour une vue d'ensemble des autres équipements à optimiser, consultez notre guide pour [économiser avec Tempo EDF](/blog/economiser-tempo-edf).

## Comprendre les heures creuses Tempo

Avant de toucher au moindre réglage, retenez les deux plages horaires de l'offre Tempo. Elles sont les mêmes tous les jours, quelle que soit la couleur :

- **Heures pleines (HP)** : de 6h à 22h
- **Heures creuses (HC)** : de 22h à 6h

C'est en heures creuses que l'électricité est la moins chère, et l'écart est spectaculaire un jour rouge :

| Plage horaire | Tarif rouge (TTC) | Coût d'un cycle de 4 kWh |
|---------------|-------------------|--------------------------|
| Heures pleines (6h-22h) | 0,7060 euros/kWh | environ 2,82 euros |
| Heures creuses (22h-6h) | 0,1575 euros/kWh | environ 0,63 euros |

Un ballon de 200 litres consomme environ **3 à 4 kWh par jour** pour réchauffer l'eau utilisée. Ce cycle coûte donc environ 2,82 euros en heures pleines un jour rouge, contre environ 0,63 euros en heures creuses. Pour savoir quels jours seront rouges et donc à éviter, gardez un oeil sur le [calendrier Tempo](/calendrier).

## Le contacteur heures creuses : votre meilleur allié

La plupart des logements équipés d'un chauffe-eau électrique disposent déjà d'un **contacteur heures creuses** dans le tableau électrique. Ce petit boîtier reçoit un signal de votre compteur (notamment le Linky) et bascule automatiquement le ballon en marche pendant les heures creuses, puis le coupe en heures pleines. C'est exactement le comportement recherché avec Tempo.

Le contacteur possède un commutateur à trois positions :

- **I (Marche forcée)** : le ballon chauffe en permanence, quelle que soit l'heure. À éviter, sauf besoin ponctuel d'eau chaude immédiate.
- **0 (Arrêt)** : le ballon ne chauffe jamais. Position idéale pour couper la chauffe un jour rouge.
- **Auto** : le ballon suit le signal heures creuses du compteur. **C'est la position par défaut recommandée.**

En laissant le contacteur sur « Auto », votre ballon chauffe naturellement entre 22h et 6h au tarif le plus bas. Pour vérifier que tout fonctionne, notre article sur [Linky et le suivi de consommation Tempo](/blog/linky-tempo-suivre-consommation) explique comment lire vos relevés et confirmer que le ballon ne se déclenche bien que la nuit.

## Programmer la chauffe sur 22h-6h

Si votre installation ne dispose pas de contacteur, ou si vous voulez un contrôle plus fin, un **programmateur** (mécanique ou connecté) intercalé sur l'alimentation du ballon fait le même travail. Le réglage idéal pour un chauffe-eau Tempo est le suivant :

- Plage de chauffe : **22h à 6h** uniquement (la totalité des heures creuses).
- Une seule relance par 24h suffit pour un ballon correctement dimensionné.
- Évitez toute chauffe entre 6h et 22h, qui tomberait en heures pleines.

Un ballon de 200 à 300 litres bien isolé n'a pas besoin de chauffer en journée : la nuit suffit à reconstituer le volume d'eau chaude consommé. Si vous manquez d'eau chaude en fin de journée, ce n'est généralement pas un problème de plage horaire mais de dimensionnement ou d'isolation, deux points abordés plus bas.

*À noter : un programmateur mécanique simple coûte une quinzaine d'euros et se branche en quelques minutes. Les modèles connectés permettent en plus de couper le ballon à distance, pratique les jours rouges.*

## Couper le ballon les jours rouges (chauffer la veille)

C'est ici que se joue l'essentiel de l'économie sur le **chauffe-eau Tempo EDF**. Le principe repose sur une propriété simple : **un ballon bien isolé conserve son eau chaude pendant 24 à 48 heures** sans apport d'énergie. Vous pouvez donc chauffer l'eau la veille et passer toute la journée rouge sans relancer le ballon.

La stratégie en trois temps :

1. **La veille au soir d'un jour rouge** (un jour bleu ou blanc), laissez le ballon chauffer en heures creuses, voire forcez une chauffe complète si votre consommation du lendemain s'annonce importante.
2. **Le jour rouge**, basculez le contacteur sur **0 (Arrêt)**, ou coupez le programmateur. Le ballon ne chauffe pas et vous puisez dans la réserve de la veille.
3. **Le soir du jour rouge**, remettez le contacteur sur « Auto ». Le ballon rechargera en heures creuses, à cheval entre le jour rouge finissant et le lendemain.

L'intérêt majeur : même si vous oubliez de couper le ballon, le pire scénario reste une chauffe en heures creuses rouges à 0,1575 euros/kWh, déjà bien plus douce que les heures pleines. Mais en coupant explicitement la journée, vous évitez toute relance intempestive en heures pleines (par exemple après une grosse consommation d'eau chaude en milieu de journée).

Le seul vrai obstacle, c'est de savoir à l'avance quels jours seront rouges. EDF n'annonce officiellement que la couleur du lendemain, chaque jour vers 11h. Pour anticiper plus loin et chauffer la veille sereinement, notre site prédit les couleurs de **J+2 à J+5**. Inscrivez-vous à nos [alertes gratuites](/#subscribe) pour être prévenu avant chaque jour rouge.

## Isoler le ballon pour prolonger la réserve d'eau chaude

Toute la stratégie « chauffer la veille, couper le jour rouge » repose sur la capacité du ballon à garder l'eau chaude longtemps. Un ballon mal isolé perd sa chaleur en quelques heures et oblige le contacteur à relancer en pleine journée. Quelques gestes simples améliorent la rétention :

- **Vérifier l'isolation d'usine** : les ballons récents sont bien isolés ; les modèles anciens perdent beaucoup plus.
- **Ajouter une jaquette isolante** : une housse pour cumulus (20 à 40 euros) réduit les pertes statiques, surtout dans un local froid (garage, cellier).
- **Isoler les premiers mètres de tuyauterie** : des manchons en mousse sur les départs d'eau chaude limitent les pertes.
- **Régler la température autour de 55-60°C** : assez chaud pour le confort, sans surchauffer inutilement.

Un ballon bien isolé qui tient ses 24 à 48 heures vous permet d'enchaîner plusieurs jours rouges consécutifs (Tempo en autorise jusqu'à 5 d'affilée) en concentrant les chauffes sur les heures creuses.

## Ballon d'eau chaude Tempo : ordres de grandeur d'économie

Mettons des chiffres approximatifs sur tout cela. Les estimations ci-dessous concernent un ballon de 200 litres consommant environ 4 kWh par jour, sur une saison Tempo comptant 22 jours rouges :

- **Cycle en heures pleines rouges** : environ 2,82 euros par jour.
- **Cycle en heures creuses (rouge ou non)** : environ 0,63 euros par jour.
- **Économie en décalant systématiquement la chauffe vers les heures creuses les jours rouges** : environ **2,19 euros par jour rouge**, soit de l'ordre de **50 euros sur la saison**.

C'est une estimation : le résultat dépend de la taille du ballon, de votre consommation d'eau chaude et de votre isolation. Mais l'ordre de grandeur est clair, et l'effort est minime une fois le contacteur réglé. Combiné à d'autres optimisations, l'effet cumulé devient significatif : notre article sur le [chauffage les jours rouges Tempo](/blog/chauffage-jour-rouge-tempo-astuces) détaille dix autres leviers complémentaires.

## FAQ : ballon d'eau chaude et Tempo

### Faut-il couper le ballon d'eau chaude tous les jours rouges ?

Ce n'est pas indispensable, mais c'est la solution la plus sûre. Si votre contacteur est réglé sur « Auto », le ballon ne chauffe déjà qu'en heures creuses (22h-6h), à 0,1575 euros/kWh même un jour rouge. Couper explicitement le ballon (position 0) la journée garantit toutefois qu'aucune relance ne se déclenche par erreur en heures pleines. Comme un ballon bien isolé tient 24 à 48 heures, chauffer la veille puis couper le jour rouge ne pose aucun problème de confort. Consultez le [calendrier Tempo](/calendrier) pour repérer les jours rouges à venir.

### Mon ballon n'a pas de contacteur heures creuses, comment faire ?

Vous avez deux options. La première consiste à faire installer un contacteur heures creuses dans votre tableau électrique : c'est l'idéal, car il se synchronise automatiquement avec le signal du compteur. La seconde, plus économique, est d'intercaler un programmateur (mécanique ou connecté) sur l'alimentation du ballon, réglé pour ne chauffer qu'entre 22h et 6h. Dans les deux cas, l'objectif est le même : ne jamais chauffer l'eau en heures pleines. Le suivi via [Linky](/blog/linky-tempo-suivre-consommation) confirmera que le ballon ne se déclenche que la nuit.

### Combien peut-on économiser avec un ballon d'eau chaude bien réglé ?

Pour un ballon de 200 litres consommant environ 4 kWh par jour, décaler la chauffe vers les heures creuses les jours rouges fait économiser de l'ordre de 2,19 euros par jour rouge, soit environ 50 euros sur les 22 jours rouges de la saison. Ce chiffre est une estimation qui varie selon la taille du ballon, votre consommation et l'isolation. Un programmateur ou une jaquette isolante se rentabilise en une saison, et le réglage du contacteur ne coûte rien.

---

*Dernière mise à jour : 21 juillet 2026. Les tarifs indiqués correspondent à l'offre Tempo EDF saison 2025-2026 (prix TTC). Consultez le [calendrier Tempo](/calendrier) pour suivre les couleurs du jour, [recevez nos alertes gratuites](/#subscribe) avant chaque jour rouge, et découvrez d'autres leviers d'économie dans notre guide pour [économiser avec Tempo EDF](/blog/economiser-tempo-edf).*
