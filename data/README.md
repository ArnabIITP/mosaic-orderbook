# Research data: sources and local setup

The raw market data is not committed to this repository. The analysis reports
and compact metric files are included; row-level observations and source data
can be regenerated locally. Keep downloaded files in the paths below.

## WSELOB-2017

Download the PEKAO HDF5 file from the official [Mendeley Data record](https://data.mendeley.com/datasets/3g4mhdp899/1)
(DOI [10.17632/3g4mhdp899.1](https://doi.org/10.17632/3g4mhdp899.1)) and save it as:

```text
data/wselob_2017/PEKAO_lob_2017_zlib.h5
```

The record identifies the data as CC BY 4.0. The analyzed source file used for
the checked-in report was 152,759,953 bytes with SHA-256
`3c418a55a492ebe2e39c8513cd7fc7e3e6827dc1a176af09fdcaad9f3485bae6`.
Attribute Adam Marszałek and follow the dataset license when redistributing
data or derived material. The report captures the source version and study
diagnostics.

The author's schema and reference implementation are retained in this folder
for the optional independent validation command in the main README.

### Five-stock generalization study

The same CC BY 4.0 dataset also contains full-year order-book files for KGHM,
PKNORLEN, PKOBP, and PZU. The five-stock archive is about 1.3 GB. Download it
from the [WSELOB-2017 record](https://data.mendeley.com/datasets/3g4mhdp899/1),
then extract the four additional `orders/*_lob_2017_zlib.h5` files into:

```text
data/wselob_2017/all_stocks/
```

Keep the existing `PEKAO_lob_2017_zlib.h5` in `data/wselob_2017/`. The source
archive is not committed. After extraction, reproduce the leave-one-stock-out
direction study with:

```powershell
python multi_stock_ml_benchmark.py --data-dir data/wselob_2017 --stride 100
python validate_wselob_multi_stock.py
```

The script writes a snapshot cache under ignored `build/`; the results include
source hashes and reconstruction diagnostics for each instrument. Follow the
CC BY 4.0 attribution and licensing requirements above.

## LOBSTER samples

Download the five official sample archives for AMZN, AAPL, GOOG, INTC, and
MSFT, dated June 21, 2012, from the [LOBSTER sample page](https://php.lobsterdata.com/info/DataSamples.php).
Extract them into the matching folders:

```text
data/lobster_amzn_2012-06-21/
data/lobster_other_tickers_2012-06-21/
```

Keep the original message and order-book CSV filenames from the archives.
Consult LOBSTER's current [access options](https://php.lobsterdata.com/info/AccessOptions.php)
and terms before redistribution or use beyond the public sample. These are
historical samples, not current market data.
