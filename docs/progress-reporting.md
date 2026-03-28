# Progress reporting

Progress dialog není napojený na fake timer. Zobrazuje data předaná běžící úlohou.

## Pole dialogu

- název operace,
- aktuální krok pipeline,
- detail kroku,
- procenta,
- ETA.

## Jak se počítá procento

- primárně se bere hodnota dodaná pipeline,
- pokud chybí, dopočítá se z `current / total`.

## Jak se počítá ETA

ETA je heuristika založená na dosavadním čase na dokončený krok:

`eta = (elapsed / current) * (total - current)`

Pokud ještě není dost dat, UI to přizná textem `ETA: heuristika čeká na více dat`.
