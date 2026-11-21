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
