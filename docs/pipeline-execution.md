# Isolated delivery execution

I bind each delivery job to an approved image digest, execution profile and resource
policy. The existing validation gate remains mandatory. A stable scheduler job ID
reattaches to retained Kubernetes Job and Pod identities after restart; conflicting
input or missing ownership evidence creates a recovery hold.

Checkout retrieves the approved full commit and removes authentication material
before source execution. Tests and rootless BuildKit run in separate application
build namespaces, without Git credentials, publication credentials, deployment
authority or control-plane storage. The builder profile explicitly accounts for
rootless BuildKit's process-isolation requirements and uses disposable storage.
There is no host Docker socket or privileged Docker fallback.

Before publication, the builder must terminate and its Job and Pods must be absent.
That absence is checked again when resuming. A separately authenticated publication
step checks OCI paths, blob checksums, platform and build/source/pipeline labels.
Only the approved repository and immutable build UUID may receive the image.

The scheduler records external identities and authoritative results in retained
ConfigMaps. Successful results are checked against the retained Job before the
control plane accepts them. Notifications alone do not establish success.
Evidence remains available until its durable acknowledgement. Ambiguous execution
must not be silently replaced.

The local cluster proof uses two nodes, separate publication accounts and actual
denied connections. Unit checks also reject altered OCI blobs, wrong build labels,
unsafe archive paths and replacement Jobs during sandbox deletion. Those checks
supplement the cluster acceptance suite; they do not replace it.
