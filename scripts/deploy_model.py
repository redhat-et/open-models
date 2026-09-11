#!/usr/bin/env python3
"""Render + apply the Kubernetes objects that serve a catalog model.

The model's `deploy:` block in catalog.slim.yaml is the single source of
truth (image, gpus, pvc, full `vllm serve` command). This script renders
Namespace (if missing) / PVC / hf-token secret / Deployment / Service /
Route from it and `oc apply`s them into the given context.

Usage:
  HF_TOKEN=hf_... python3 scripts/deploy_model.py <model-id> \
      --context <kube-context> [--dry-run] [--wait]

After it reports the route URL, paste it into the model's `base_url:`
(re-point the old URL if this replaces one), then `make gen && make deploy`.
`hf_token` is never stored in the catalog - it comes from $HF_TOKEN.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

NS = "octo-models-compute"


def sh(*args: str, check: bool = True) -> str:
    out = subprocess.run(args, capture_output=True, text=True)
    if check and out.returncode != 0:
        sys.exit(f"command failed: {' '.join(args)}\n{out.stderr.strip()}")
    return out.stdout


def oc(ctx: str, *args: str) -> str:
    return sh("oc", f"--context={ctx}", *args)


def render(model: dict) -> list[tuple[str, dict]]:
    d = model["deploy"]
    host_env = model.get("host", "unknown")
    name = d.get("name") or model["id"]
    pvc = d.get("pvc") or {}
    command = d.get("command")
    if not command:
        sys.exit(f"model {model['id']} has no deploy.command")
    gpus = str(d.get("gpus", 1))

    pvc_obj = {
        "apiVersion": "v1", "kind": "PersistentVolumeClaim",
        "metadata": {"name": pvc.get("name", f"{name}-hf-cache"), "namespace": NS,
                     "labels": {"app": name}},
        "spec": {"accessModes": ["ReadWriteOnce"],
                 "storageClassName": pvc.get("storage_class", "gp3-csi"),
                 "resources": {"requests": {"storage": pvc.get("size", "200Gi")}}},
    }
    secret = {
        "apiVersion": "v1", "kind": "Secret",
        "metadata": {"name": "hf-token", "namespace": NS},
        "type": "Opaque",
        "stringData": {"token": os.environ.get("HF_TOKEN", "")},
    }
    volumes = [{"name": "hf-cache", "persistentVolumeClaim":
                {"claimName": pvc.get("name", f"{name}-hf-cache")}},
               {"name": "dev-shm", "emptyDir": {"medium": "Memory", "sizeLimit": "4Gi"}}]
    mounts = [{"mountPath": pvc.get("mount", "/cache"), "name": "hf-cache"},
              {"mountPath": "/dev/shm", "name": "dev-shm"}]
    env = [{"name": "HF_TOKEN", "valueFrom":
            {"secretKeyRef": {"name": "hf-token", "key": "token"}}}]
    container = {
        "name": name,
        "image": d["image"],
        "imagePullPolicy": "IfNotPresent",
        "command": ["bash", "-c", command],
        "env": env + (d.get("env") or []),
        "ports": [{"containerPort": p, "protocol": "TCP"} for p in d.get("ports", [8000])],
        "resources": {
            "limits": {"nvidia.com/gpu": gpus,
                       "ephemeral-storage": d.get("ephemeral_storage", "250Gi")},
            "requests": {"nvidia.com/gpu": gpus,
                         "ephemeral-storage": d.get("ephemeral_storage", "250Gi")},
        },
        "volumeMounts": mounts,
    }
    spec = {"restartPolicy": "Always", "terminationGracePeriodSeconds": 30,
            "volumes": volumes, "containers": [container]}
    if d.get("tolerations"):
        spec["tolerations"] = d["tolerations"]
    if d.get("node_selector"):
        spec["nodeSelector"] = d["node_selector"]
    deploy = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": name, "namespace": NS,
                     "labels": {"app": name, "octo.io/env": host_env}},
        "spec": {
            "replicas": 1, "revisionHistoryLimit": 1,
            "progressDeadlineSeconds": d.get("progress_deadline", 3600),
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app": name}},
            "template": {"metadata": {"labels": {"app": name,
                                                  "octo.io/env": host_env}},
                         "spec": spec},
        },
    }
    svc = {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {"name": name, "namespace": NS, "labels": {"app": name}},
        "spec": {"selector": {"app": name},
                 "ports": [{"port": p, "targetPort": p} for p in d.get("ports", [8000])]},
    }
    route = {
        "apiVersion": "route.openshift.io/v1", "kind": "Route",
        "metadata": {"name": name, "namespace": NS, "labels": {"app": name}},
        "spec": {"to": {"kind": "Service", "name": name},
                 "port": {"targetPort": d.get("ports", [8000])[0]},
                 "tls": {"termination": "edge",
                         "insecureEdgeTerminationPolicy": "Redirect"}},
    }
    return [("pvc", pvc_obj), ("secret", secret), ("deployment", deploy),
            ("service", svc), ("route", route)]


def wait_ready(ctx: str, name: str, timeout_s: int) -> None:
    sh("oc", f"--context={ctx}", "rollout", "status",
       f"deploy/{name}", "-n", NS, f"--timeout={timeout_s}s")


def wait_serving(ctx: str, name: str, model: dict, timeout_s: int) -> str:
    """Pod is Running but vLLM may take 15-25 min (weights + graphs). Poll
    the in-cluster service /health until it answers 200."""
    route = oc(ctx, "get", "route", name, "-n", NS, "-o",
               "jsonpath={.spec.host}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = subprocess.run(["curl", "-s", "-m", "10", "-o", "/dev/null",
                            "-w", "%{http_code}",
                            f"https://{route}/health"])
        if r.stdout == "200":
            return f"https://{route}"
        time.sleep(60)
    sys.exit(f"pod rolled but {route}/health never returned 200 in {timeout_s}s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model")
    ap.add_argument("--context", required=True, help="oc context on the target cluster")
    ap.add_argument("--dry-run", action="store_true", help="print manifests, apply nothing")
    ap.add_argument("--wait", action="store_true", help="block until the model serves")
    args = ap.parse_args()

    cat = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "catalog.slim.yaml"))
    model = next((m for m in cat["models"] if m["id"] == args.model), None)
    if not model:
        sys.exit(f"no such model in catalog: {args.model}")
    if not model.get("deploy"):
        sys.exit(f"model {args.model} has no deploy: block")
    if not os.environ.get("HF_TOKEN"):
        sys.exit("HF_TOKEN env var is required (the deploy: block never stores it)")

    objs = render(model)
    docs = "\n---\n".join(yaml.safe_dump(o, sort_keys=False) for _, o in objs)
    if args.dry_run:
        print(docs)
        return 0

    if not sh("oc", f"--context={args.context}", "get", "ns", NS, check=False).strip():
        oc(args.context, "create", "ns", NS)
    # apply each object separately so a pre-existing PVC with a different
    # claim name errors out loudly instead of binding a surprise new EBS
    for kind, obj in objs:
        p = subprocess.run(["oc", f"--context={args.context}", "apply", "-f", "-"],
                           input=yaml.safe_dump(obj), text=True, capture_output=True)
        if p.returncode != 0 and kind == "pvc":
            sys.exit(f"PVC issue (existing EBS won't auto-resize/rename): {p.stderr.strip()}")
        if p.returncode != 0:
            sys.exit(f"{kind} apply failed: {p.stderr.strip()}")

    name = objs[2][1]["metadata"]["name"]
    if args.wait:
        wait_ready(args.context, name, 300)
        url = wait_serving(args.context, name, model, 3600)
        print(f"\n{model['id']} serving at: {url}")
        print("Next: set base_url in catalog.slim.yaml to this URL, then "
              "`make gen && make deploy APP=1`.")
    else:
        print(f"applied pvc/secret/deployment/service/route for {name} "
              f"(ns {NS}); re-run with --wait to block until it serves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
