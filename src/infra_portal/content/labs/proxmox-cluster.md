---
title: A Proxmox cluster on small machines
kind: experiment
date: 2026-10-08
link: https://github.com/PragalvaXFREZ/dwit-in-house-infra/blob/main/reports/2026-10-08-standalone-proxmox.md
summary: At idle, Proxmox alone used about 1.32 GiB of RAM, so we dropped the cluster.
---
Hardware: pve01 (8 GB), plus one 4 GB and one 2 GB machine.

At idle, Proxmox alone used about 1.32 GiB of RAM. That leaves too little memory on the 4 GB and 2 GB
machines for a cluster to be useful, so we dropped the cluster.

pve01 stays as a standalone Proxmox host for running VMs. The 4 GB and 2 GB machines run Debian 13
directly, which is much lighter, for experimenting with other projects.
