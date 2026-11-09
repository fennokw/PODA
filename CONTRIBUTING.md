# Contributing

This repository owns the Electron launcher, its private backend adapter, runtime setup, and native packaging. Change shared UI, API, connectors, and browser hosting in [PODA Web](https://github.com/fennokw/PODA-Web).

`desktop/backend-lock.json` pins the shared runtime. Stage it with `npm run stage:backend` in `desktop`. Set `PODA_WEB_CHECKOUT` to a local Git checkout containing the pinned commit to stage without fetching. The packager reads the exact commit tree, so local edits in that checkout do not affect the bundle.
