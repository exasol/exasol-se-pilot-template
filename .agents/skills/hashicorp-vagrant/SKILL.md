---
name: hashicorp-vagrant
description: Use when asked to create, audit, troubleshoot, or document HashiCorp Vagrant local development environments, Vagrantfiles, boxes, providers, provisioning, SSH access, or teardown workflows for repeatable workshop labs.
---

# HashiCorp Vagrant

Use this skill for Vagrant-based local development or workshop labs, including Vagrantfiles, boxes, providers, provisioning, networking, shared folders, SSH, and cleanup.

## Safety Rules

- Do not run `vagrant up` if it will create VMs without user awareness of local disk/CPU/memory impact.
- Do not run `vagrant destroy` without explicit confirmation.
- Do not commit `.vagrant/`, provider state, generated keys, synced data, or VM artifacts.
- Review shell provisioners for secret echoing, unsafe downloads, and commands that write credentials into images.

## Workflow

1. Inspect environment:

```bash
vagrant version
vagrant status
vagrant global-status --prune
```

2. Review Vagrantfile before starting VMs:

```bash
ruby -c Vagrantfile
rg -n 'config.vm.box|provider|provision|network|synced_folder|private_key|password|token|secret' Vagrantfile
```

3. Start or reprovision only after confirming provider and resource footprint:

```bash
vagrant up --provider <provider>
vagrant provision
vagrant reload --provision
```

4. Troubleshoot:

```bash
vagrant ssh-config
vagrant ssh
vagrant status
```

5. Cleanup only after confirmation:

```bash
vagrant halt
vagrant destroy
```

## Review Heuristics

- Prefer pinned, trusted boxes and explicit providers.
- Note that boxes are provider-specific; the same box name may exist for different providers.
- Keep provisioning repeatable and non-interactive.
- Check host port collisions, private network ranges, synced-folder paths, and resource sizing.
- For workshop labs, make first-run setup deterministic and teardown obvious.

## Official References

- IBM HashiCorp product page: https://www.ibm.com/products/hashicorp
- Vagrant boxes: https://developer.hashicorp.com/vagrant/docs/boxes
- Vagrant providers basic usage: https://developer.hashicorp.com/vagrant/docs/providers/basic_usage
- Vagrant provisioning: https://developer.hashicorp.com/vagrant/docs/provisioning
- `vagrant up` command: https://developer.hashicorp.com/vagrant/docs/cli/up
