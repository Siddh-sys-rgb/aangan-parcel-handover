# Working browser verification

The application was exercised locally on macOS with Python 3.12, through the actual browser UI and fictional demo data. These screenshots show the working product, not generated mockups.

- Rejected an incorrect pickup code without handing over the parcel.
- Handed over parcel 1 using its correct code; code changed to used and one receipt/event was visible.
- Restarted the server and signed back in: the collected record remained stored.

All six apps were checked at a 390×844 viewport; this app's document width was 390px with no horizontal overflow. The temporary viewport was reset after the check. The fresh final app load reported no JavaScript errors. Desktop and mobile captures can show different points in the walkthrough.

Automated regression suite: **63 passing tests**. The README describes test scope and measured coverage. These checks do not establish production scale or complete security coverage.
