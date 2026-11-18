# Installed HTTPS notification proof

I ran these supplemental checks against both installed acceptance systems at
revision 437d2ca3debcdc6438dd15568fc62f4d2cc69ba6, after their 33 fault cases
settled and before cleanup. The evidence is in delivery-proof.json under each
run's http_notifications entry.

To reproduce, copy both Python files into the checkout's .runlogs directory.
Prepare the two acceptance outputs as .runlogs/pipeline-437d2ca3-one and
.runlogs/pipeline-437d2ca3-two using the packaged acceptance harness, and complete
their side-effect-faults scenarios. With Python 3.12, cryptography, kubectl and
Helm available, run prove_pipeline_http_notifications_437d2ca3.py.

These are destructive process-interruption checks for isolated acceptance
installations. They create a private local certificate and authenticated HTTPS
fixture, temporarily configure the optional sink, and terminate the acceptance
API process at observed boundaries. They preserve application releases, restore
the original Helm settings, and leave fixture resources for the ownership-aware
acceptance cleanup. Credentials remain in local private files and Kubernetes
Secrets. The retained demonstration does not enable this test sink.
