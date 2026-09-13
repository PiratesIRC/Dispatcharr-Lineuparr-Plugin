# Changelog

Newest first. Versions are calver Major.YY.DDDHHMM (UTC).

## 1.26.2561550 (2026-09-13)

### Added

- Two contributed lineups, taking the built-in count from 20 to 22.
  `QA_beIN-MENA_lineup.json` carries beIN's Middle East and North Africa
  package, 168 channels across 9 categories, contributed by Adam through the
  Dispatcharr Discord server. `NL_KPN_lineup.json` carries KPN Interactive TV
  in the Netherlands, 290 television and radio channels across 29 categories,
  contributed by ZenimNL in issue #22.

### Fixed

- The country filter now applies to a Qatar lineup. `QA` was missing from the
  recognized country codes, so stream matching skipped the filter entirely for
  the beIN MENA lineup and an Australian, Belgian or French beIN feed could
  attach to one of its channels. Only `QA` was added: the lineup is regional
  and the filter is single country, so recognizing the other Middle East and
  North Africa codes would make it reject streams it legitimately carries.

### Known

- In the beIN MENA lineup, `QBC 4K` at 343 and `QBC HD` at 344 cannot be told
  apart, because matching removes quality tags from a name. Turning on
  Quality Aware Stream Matching separates them.
- In the KPN lineup, four regional radio stations share a name with the
  television channel of the same broadcaster, separated only by an `HD`
  suffix that matching removes: Omroep Brabant, Omroep Zeeland, Omrop
  Fryslan and XITE Klassiek. Four of its categories are empty and will create
  empty channel groups.

## 1.26.2481702 (2026-09-05)

- See the GitHub release for v1.26.2481702.

## 1.26.2421451 (2026-08-30)

- See the GitHub release for v1.26.2421451.

## 1.26.2291211 (2026-08-17)

- See the GitHub release for v1.26.2291211.

## 1.26.2271740 (2026-08-15)

- See the GitHub release for v1.26.2271740.

## 1.26.2241618 (2026-08-12)

- See the GitHub release for v1.26.2241618.

## 1.26.2171315 (2026-08-05)

- See the GitHub release for v1.26.2171315.

## 1.26.2142327 (2026-08-02)

- See the GitHub release for v1.26.2142327.

## 1.26.1931114 (2026-07-12)

- See the GitHub release for v1.26.1931114.

## 1.26.1791747 (2026-06-28)

- See the GitHub release for v1.26.1791747.

## 1.26.1641222 (2026-06-13)

- See the GitHub release for v1.26.1641222.

## 1.26.1582125 (2026-06-07)

- See the GitHub release for v1.26.1582125.

## 1.26.1431300 (2026-05-23)

- See the GitHub release for v1.26.1431300.

## 1.26.1421711 (2026-05-22)

- See the GitHub release for v1.26.1421711.

## 1.26.1370103 (2026-05-16)

- See the GitHub release for v1.26.1370103.

## 1.26.1091027 (2026-04-19)

- See the GitHub release for v1.26.1091027.

## 1.26.1021446 (2026-04-12)

- See the GitHub release for v1.26.1021446.

## 1.26.1001146 (2026-04-10)

- See the GitHub release for v1.26.1001146.

## 1.26.9520 (2026-04-05)

- See the GitHub release for v1.26.9520.
