"""有向图工具：Tarjan 强连通分量（SCC）。

互相持股 / 交叉持股会形成规模大于 1（或带自环）的 SCC，
先识别这些连通部分，再建立联立方程，是求解与结果解释的第一步。
"""
from collections import defaultdict


def tarjan_scc(nodes: list, edges: list[tuple]) -> list[list]:
    """nodes: 节点 id 列表；edges: (from_id, to_id) 有向边（重复边自动去重）。

    返回 SCC 列表，每个 SCC 是节点 id 列表；单点且无自环的 SCC 不返回。
    """
    adj: dict = defaultdict(set)
    node_set = set(nodes)
    for src, dst in edges:
        if src in node_set and dst in node_set:
            adj[src].add(dst)

    index_counter = [0]
    stack: list = []
    on_stack: set = set()
    indices: dict = {}
    lowlink: dict = {}
    result: list[list] = []

    def strongconnect(v):
        indices[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj[v]:
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            component = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            # 只保留真正的互相持股部分：规模>1，或单点带自环
            if len(component) > 1 or v in adj[v]:
                result.append(sorted(component))

    for node in nodes:
        if node not in indices:
            strongconnect(node)
    return result


def all_sccs(nodes: list, edges: list[tuple]) -> list[list]:
    """返回全部 SCC（含单点），供封闭性分析使用。"""
    adj: dict = defaultdict(set)
    node_set = set(nodes)
    for src, dst in edges:
        if src in node_set and dst in node_set:
            adj[src].add(dst)

    index_counter = [0]
    stack: list = []
    on_stack: set = set()
    indices: dict = {}
    lowlink: dict = {}
    result: list[list] = []

    def strongconnect(v):
        indices[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj[v]:
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            component = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            result.append(sorted(component))

    for node in nodes:
        if node not in indices:
            strongconnect(node)
    return result
