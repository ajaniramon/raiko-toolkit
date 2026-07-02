"""In-process mock of a Kubernetes CLI/API for the Frontier tier.

Mirrors the style of mock_atlassian.py: seeded, deterministic, in-process
state; every method returns a plain tool-result STRING; unknown inputs
return an "ERROR: ..." string, never raise.

Planted incident: pod `checkout-api-7d9f8b6c4-x2x9k` in namespace `prod` is
CrashLoopBackOff with 17 restarts, OOMKilled (exit 137). Its previous
container logs carry the root cause: an out-of-memory crash after PR #47
reduced the memory limit from 512Mi to 128Mi (see mock_git.py). The other
pods in the seed (including the sibling replica of checkout-api and the
staging replicas, still on the older 2.3.9 image with 512Mi) are healthy
decoys: their logs never mention memory, so an agent must trace the OOM
back to the specific incident pod rather than pattern-match on the service
name.
"""
import copy

DEFAULT_SEED = {
    "namespaces": ["prod", "staging"],
    "pods": [
        {
            "name": "checkout-api-7d9f8b6c4-x2x9k",
            "namespace": "prod",
            "status": "CrashLoopBackOff",
            "restarts": 17,
            "image": "checkout-api:2.4.1",
            "mem_limit": "128Mi",
            "last_exit": "OOMKilled (137)",
        },
        {
            "name": "checkout-api-7d9f8b6c4-k7m2p",
            "namespace": "prod",
            "status": "Running",
            "restarts": 2,
            "image": "checkout-api:2.4.1",
            "mem_limit": "128Mi",
            "last_exit": "-",
        },
        {
            "name": "payments-api-66c8d-p4q1r",
            "namespace": "prod",
            "status": "Running",
            "restarts": 9,
            "image": "payments-api:1.9.2",
            "mem_limit": "256Mi",
            "last_exit": "-",
        },
        {
            "name": "search-api-5b9c7-t8u3v",
            "namespace": "prod",
            "status": "Running",
            "restarts": 0,
            "image": "search-api:3.1.0",
            "mem_limit": "512Mi",
            "last_exit": "-",
        },
        {
            "name": "checkout-api-59e2a1b77-s6w4z",
            "namespace": "staging",
            "status": "Running",
            "restarts": 0,
            "image": "checkout-api:2.3.9",
            "mem_limit": "512Mi",
            "last_exit": "-",
        },
        {
            "name": "checkout-api-59e2a1b77-h1j5n",
            "namespace": "staging",
            "status": "Running",
            "restarts": 1,
            "image": "checkout-api:2.3.9",
            "mem_limit": "512Mi",
            "last_exit": "-",
        },
    ],
    "events": {
        "prod": [
            "Warning  BackOff    pod/checkout-api-7d9f8b6c4-x2x9k   BackOff restarting failed container checkout-api",
            "Warning  Killing    pod/checkout-api-7d9f8b6c4-x2x9k   Killing container checkout-api: OOMKilled",
            "Normal   Scheduled  pod/checkout-api-7d9f8b6c4-k7m2p   Successfully assigned prod/checkout-api-7d9f8b6c4-k7m2p",
        ],
        "staging": [
            "Normal   Scheduled  pod/checkout-api-59e2a1b77-s6w4z   Successfully assigned staging/checkout-api-59e2a1b77-s6w4z",
        ],
    },
    "deployments": {
        "checkout-api": {"namespace": "prod", "replicas": 2, "revision": 12},
    },
    "logs": {
        "checkout-api-7d9f8b6c4-x2x9k": {
            "current": [
                "INFO  checkout-api starting up (image 2.4.1)",
                "INFO  connected to cart-cache",
                "WARN  container restarted by kubelet after previous crash",
            ],
            "previous": [
                "INFO  checkout-api starting up (image 2.4.1)",
                "INFO  handling checkout request for cart 88213",
                "fatal: out of memory - heap limit 128Mi exceeded during cart serialization",
                "java.lang.OutOfMemoryError: Java heap space",
                "INFO  process exiting (signal: OOM)",
            ],
        },
        "checkout-api-7d9f8b6c4-k7m2p": {
            "current": [
                "INFO  checkout-api starting up (image 2.4.1)",
                "INFO  connected to cart-cache",
                "INFO  serving traffic normally",
            ],
            "previous": [
                "INFO  checkout-api starting up (image 2.4.1)",
                "INFO  graceful shutdown requested",
            ],
        },
        "payments-api-66c8d-p4q1r": {
            "current": [
                "INFO  payments-api starting up (image 1.9.2)",
                "INFO  connected to ledger-db",
                "INFO  processed payment batch ok",
            ],
            "previous": [
                "INFO  payments-api starting up (image 1.9.2)",
                "INFO  connected to ledger-db",
            ],
        },
        "search-api-5b9c7-t8u3v": {
            "current": [
                "INFO  search-api starting up (image 3.1.0)",
                "INFO  index warmed",
                "INFO  serving queries normally",
            ],
            "previous": [],
        },
        "checkout-api-59e2a1b77-s6w4z": {
            "current": [
                "INFO  checkout-api starting up (image 2.3.9)",
                "INFO  serving staging traffic",
            ],
            "previous": [],
        },
        "checkout-api-59e2a1b77-h1j5n": {
            "current": [
                "INFO  checkout-api starting up (image 2.3.9)",
                "INFO  serving staging traffic",
            ],
            "previous": [
                "INFO  checkout-api starting up (image 2.3.9)",
                "INFO  graceful shutdown requested",
            ],
        },
    },
}


class MockK8s:
    """Deterministic, in-process stand-in for `kubectl`-style tools.

    Rebuilt fresh per task; `seed` defaults to DEFAULT_SEED (fixtures_frontier
    will later pass an explicit, possibly extended, seed).
    """

    def __init__(self, seed=None):
        seed = seed or DEFAULT_SEED
        self._namespaces = list(seed["namespaces"])
        self._pods = [dict(p) for p in seed["pods"]]
        self._events = copy.deepcopy(seed["events"])
        self._deployments = copy.deepcopy(seed["deployments"])
        self._logs = copy.deepcopy(seed["logs"])

    def _find_pod(self, name, namespace):
        name = (name or "").strip()
        namespace = (namespace or "").strip()
        for p in self._pods:
            if p["name"] == name and p["namespace"] == namespace:
                return p
        return None

    def list_pods(self, namespace):
        namespace = (namespace or "").strip()
        if namespace not in self._namespaces:
            return f"ERROR: not found: namespace {namespace}"
        rows = [p for p in self._pods if p["namespace"] == namespace]
        header = f"{'NAME':<32}{'STATUS':<20}{'RESTARTS':<10}{'IMAGE'}"
        lines = [f"NAMESPACE: {namespace}", header]
        for p in rows:
            lines.append(
                f"{p['name']:<32}{p['status']:<20}{p['restarts']:<10}{p['image']}"
            )
        return "\n".join(lines)

    def get_pod(self, name, namespace="prod"):
        p = self._find_pod(name, namespace)
        if not p:
            return f"ERROR: not found: pod {name} in namespace {namespace}"
        return (
            f"Pod: {p['name']}\n"
            f"Namespace: {p['namespace']}\n"
            f"Status: {p['status']}\n"
            f"Restarts: {p['restarts']}\n"
            f"Image: {p['image']}\n"
            f"Memory limit: {p['mem_limit']}\n"
            f"Last exit reason: {p['last_exit']}"
        )

    def logs(self, name, namespace="prod", previous=False):
        p = self._find_pod(name, namespace)
        if not p:
            return f"ERROR: not found: pod {name} in namespace {namespace}"
        section = "previous" if previous else "current"
        lines = self._logs.get(p["name"], {}).get(section, [])
        if not lines:
            return f"ERROR: no {section} logs available for pod {name}"
        return "\n".join(lines)

    def events(self, namespace="prod"):
        namespace = (namespace or "").strip()
        if namespace not in self._namespaces:
            return f"ERROR: not found: namespace {namespace}"
        lines = self._events.get(namespace, [])
        if not lines:
            return f"No events in namespace {namespace}."
        return "\n".join(lines)

    def rollout_status(self, deployment, namespace="prod"):
        deployment = (deployment or "").strip()
        namespace = (namespace or "").strip()
        d = self._deployments.get(deployment)
        if not d or d["namespace"] != namespace:
            return f"ERROR: not found: deployment {deployment} in namespace {namespace}"
        return (
            f"deployment.apps/{deployment}\n"
            f"REVISION  REPLICAS  READY\n"
            f"{d['revision']:<10}{d['replicas']:<10}{d['replicas']}\n\n"
            f"rollout status: successfully rolled out (revision {d['revision']}, {d['replicas']} replicas)"
        )
