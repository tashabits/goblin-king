## Summary

- 

## Local Proof

Paste exact local output for the checks that apply:

```text
python -m pytest
python -m ruff check .
```

Admin/frontend changes also need:

```text
cd admin-ui && npm test -- --run
cd admin-ui && npm run build
```

Docker, Helm, runtime, scheduler, validation, resource-policy, or admin behavior changes
must include the relevant local smoke proof. GitHub Actions are not the required quality
gate yet.

## Safety Checklist

- [ ] This preserves the container-first goblin model.
- [ ] This does not weaken the mandatory validation gate.
- [ ] This does not broaden Docker socket access to goblin task containers.
- [ ] This updates docs/proof tables when public behavior or adopter guidance changes.
- [ ] Deferred or follow-up work is called out clearly.

