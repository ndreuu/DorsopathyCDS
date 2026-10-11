# Thermimage radiometry adaptation

`radiometry.py` contains conversion equations adapted from Glenn J. Tattersall,
Thermimage `R/raw2temp.R`, through the previously existing local research
converter `var/real-inputs/thermography/prepare_thermal.py`.

Original source: https://github.com/gtatters/Thermimage/blob/master/R/raw2temp.R
Reviewed content SHA256:
`2c1744363d8f36318d06955f1004dd98329e5277a9a6fd23d725e1b4850b8916`.
Thermimage DESCRIPTION declares GPL (>=2), and upstream LICENSE.md contains
GPLv3. This adapted component is released as GPL-3.0-or-later. A copy of the
license is in `THERMIMAGE_LICENSE.txt`; there is no warranty.

Modified on 2026-10-10 for the raw FLIR provider: Python/stdlb implementation,
mandatory file-specific constants, per-setting provenance, finite/domain
checks, invalid-pixel nulls and QC. No upstream camera defaults are used.
Distribution of this adaptation and a combined work must comply with GPL;
process isolation does not itself remove licensing obligations.

ExifTool is an external, separately configured tool; no ExifTool parser source
is bundled. Its official README declares the Perl Artistic License OR GPL.
The public back image used for local acceptance has unknown redistribution
rights and is not bundled with this code.
