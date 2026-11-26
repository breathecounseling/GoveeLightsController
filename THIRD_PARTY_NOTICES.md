# Third-party notices

GoveeLightsController has no runtime dependencies beyond the Python standard library.
It includes or adapts the following third-party work.

## QR Code Generator for JavaScript

* File: `goveelights/web/vendor/qrcode.js` (included unmodified, with its header)
* Copyright (c) 2009 Kazuhiko Arase, <http://www.d-project.com/>
* Licence: MIT (text below)
* "QR Code" is a registered trademark of DENSO WAVE INCORPORATED.

## homebridge-govee-pixel-zones

* Upstream: <https://github.com/rileysheehan/homebridge-govee-pixel-zones>
* Used in: `goveelights/protocol.py` (the `ptReal` Finger Sketch packet framing is ported
  from that project's protocol code; the test suite checks it against that project's
  reference capture)
* Copyright (c) 2026 Riley Sheehan
* Licence: MIT (text below). Licence verified on the upstream repository in November 2026.

## Protocol information from community reports

The realtime (`razer`) packet format follows the findings published by community members
in [govee-local-api issue #404](https://github.com/Galorhallen/govee-local-api/issues/404).
No code is copied from that project. Govee's official LAN API documentation covers only
the documented commands (scan, turn, brightness, colorwc, devStatus).

---

## MIT licence text (applies to both MIT components above)

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
