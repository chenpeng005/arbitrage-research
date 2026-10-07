(function () {
  const findings = document.getElementById("findings");
  if (!findings) return;

  const categories = [
    {
      key: "corporate-action",
      label: "公司行动 / 特殊事件",
      match: /要约|现金选择权|主动退市|退市|私有化|吸收合并|换股|并购|重组|收购|余股|特别股息|回购|定增|停产|股权|控制权|B转H|H股/
    },
    {
      key: "fund-arbitrage",
      label: "ETF / LOF / QDII",
      match: /ETF|LOF|QDII|纳指|标普|申赎|净值|场内基金|联接基金|基金溢价|折价赎回/
    },
    {
      key: "convertible-bond",
      label: "可转债",
      match: /转债|配债|强赎|不强赎|下修|回售/
    },
    {
      key: "new-issue",
      label: "北交所 / 新股",
      match: /北交所|新股|打新|中签|申购门槛/
    },
    {
      key: "rules-execution",
      label: "规则 / 账户 / 执行",
      match: /券商|账户|交易限制|结算|申报|行权|数据异常|数据错误|规则变化|席位|通道/
    },
    {
      key: "other",
      label: "其他机会 / 异常",
      match: /.*/
    }
  ];

  function normalized(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[\s·•，。；：、（）()【】\[\]《》“”‘’'"—_-]+/g, "")
      .trim();
  }

  function cardSignal(card) {
    const title = card.querySelector(".object-head h2")?.textContent || "";
    const nodes = Array.from(card.querySelectorAll(".node-title"))
      .map(function (node) { return node.textContent || ""; })
      .join(" ");
    return title + " " + nodes;
  }

  function classify(card) {
    const signal = cardSignal(card);
    return categories.find(function (category) {
      return category.match.test(signal);
    }) || categories[categories.length - 1];
  }

  function mergeDuplicateObjectCards(cards) {
    const kept = [];
    const byTitle = new Map();

    cards.forEach(function (card) {
      const title = normalized(card.querySelector(".object-head h2")?.textContent);
      if (!title || !byTitle.has(title)) {
        byTitle.set(title || String(kept.length), card);
        kept.push(card);
        return;
      }

      const target = byTitle.get(title);
      const targetNodes = target.querySelector(".object-nodes");
      const extraNodes = card.querySelectorAll(".object-node");
      extraNodes.forEach(function (node) {
        targetNodes.appendChild(node);
      });
      card.remove();
    });

    return kept;
  }

  function mergeNodeSources(target, duplicate) {
    const targetSources = new Set(
      Array.from(target.querySelectorAll(".node-source")).map(function (source) {
        return normalized(source.textContent);
      })
    );
    duplicate.querySelectorAll(".node-source").forEach(function (source) {
      const key = normalized(source.textContent);
      if (!key || targetSources.has(key)) return;
      const clone = source.cloneNode(true);
      clone.classList.add("merged-source");
      target.appendChild(clone);
      targetSources.add(key);
    });
  }

  function dedupeNodes(card) {
    const seen = new Map();
    Array.from(card.querySelectorAll(".object-node")).forEach(function (node) {
      const title = normalized(node.querySelector(".node-title")?.textContent);
      const fact = normalized(node.querySelector(".node-fact")?.textContent);
      const key = title + "|" + fact;
      if (!title || !seen.has(key)) {
        seen.set(key, node);
        return;
      }
      mergeNodeSources(seen.get(key), node);
      node.remove();
    });
  }

  function countNodes(cards) {
    return cards.reduce(function (sum, card) {
      return sum + card.querySelectorAll(".object-node").length;
    }, 0);
  }

  function renderIndex(groups) {
    const nav = document.createElement("nav");
    nav.className = "category-index";
    nav.setAttribute("aria-label", "今日发现分类");

    groups.forEach(function (group) {
      const link = document.createElement("a");
      link.href = "#category-" + group.category.key;
      link.className = "category-index-item";
      link.innerHTML =
        "<strong>" + group.category.label + "</strong>" +
        "<span>" + group.cards.length + " 对象 · " + countNodes(group.cards) + " 条</span>";
      nav.appendChild(link);
    });
    return nav;
  }

  function structureFindings() {
    if (findings.dataset.structured === "category-v1") return;
    const rawCards = Array.from(findings.children).filter(function (node) {
      return node.classList && node.classList.contains("object-card");
    });
    if (!rawCards.length) return;

    const cards = mergeDuplicateObjectCards(rawCards);
    cards.forEach(dedupeNodes);

    const grouped = new Map();
    categories.forEach(function (category) {
      grouped.set(category.key, {category: category, cards: []});
    });
    cards.forEach(function (card) {
      const category = classify(card);
      grouped.get(category.key).cards.push(card);
    });

    const activeGroups = categories
      .map(function (category) { return grouped.get(category.key); })
      .filter(function (group) { return group.cards.length > 0; });

    findings.innerHTML = "";
    findings.appendChild(renderIndex(activeGroups));

    activeGroups.forEach(function (group) {
      const section = document.createElement("section");
      section.className = "category-section";
      section.id = "category-" + group.category.key;

      const header = document.createElement("div");
      header.className = "category-head";
      header.innerHTML =
        "<h2>" + group.category.label + "</h2>" +
        "<span>" + group.cards.length + " 个对象 · " + countNodes(group.cards) + " 条信息</span>";
      section.appendChild(header);

      const body = document.createElement("div");
      body.className = "category-objects";
      group.cards.forEach(function (card) { body.appendChild(card); });
      section.appendChild(body);
      findings.appendChild(section);
    });

    findings.dataset.structured = "category-v1";
  }

  let scheduled = false;
  const observer = new MutationObserver(function () {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(function () {
      scheduled = false;
      structureFindings();
    });
  });

  observer.observe(findings, {childList: true});
  structureFindings();
})();
