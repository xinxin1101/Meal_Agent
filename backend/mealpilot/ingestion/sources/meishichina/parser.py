"""Deterministic HTML parsing for MeishiChina desktop and mobile recipe pages."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse, urlunparse

from mealpilot.domain.models import CookingStep

from .models import RawMeishiChinaRecipe, RawRecipeIngredient


ALLOWED_HOSTS = {"home.meishichina.com", "m.meishichina.com"}
RECIPE_PATHS = (re.compile(r"^/recipe-(\d+)\.html$"), re.compile(r"^/recipe/(\d+)/$"))
GROUPS = {"主料": "main", "辅料": "secondary", "配料": "seasoning", "调料": "seasoning"}
CHALLENGE_MARKERS = ("challenge-error-text", "/cdn-cgi/challenge-platform/", "Enable JavaScript and cookies")
IMAGE_DEPENDENT = re.compile(r"(?:如图|见图|图中|下图|上图|照图|这是.{0,10}(?:样子|状态)|^(?:成品|完成图|装盘图)[。.!！]?$)")
MEDICAL_CLAIM = re.compile(r"(?:治疗|治愈|疗效|抗癌|降血压|降血糖|预防.{0,8}(?:病|癌)|患者|疾病|孕妇|婴儿|产妇)")
COPYRIGHT_TEXT = re.compile(r"[^。\n]{0,80}(?:禁止其他平台或个人转载|未经[^。\n]{0,30}(?:许可|授权)[^。\n]{0,40})[。.]?")
STEP_PREFIX = re.compile(r"^\s*(\d+)\s*[.、．:]\s*")
AMOUNT_SUFFIX = re.compile(
    r"(.+?)(适量|少许|若干|\d+(?:\.\d+)?\s*(?:克|g|kg|毫升|ml|个|只|根|块|片|勺|匙|碗|杯|听|斤|两))$",
    re.IGNORECASE,
)


class RecipeParseError(ValueError):
    pass


class AccessChallengeError(RecipeParseError):
    pass


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [*self.text_parts]
        for child in self.children:
            parts.append(child.text())
        return clean_text(" ".join(parts))

    def descendants(self) -> list["Node"]:
        result: list[Node] = []
        for child in self.children:
            result.append(child)
            result.extend(child.descendants())
        return result

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())


class TreeBuilder(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.stack[-1].text_parts.append(data)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_tree(html: str) -> Node:
    if any(marker.lower() in html.lower() for marker in CHALLENGE_MARKERS):
        raise AccessChallengeError("SOURCE_ACCESS_CHALLENGE")
    builder = TreeBuilder()
    builder.feed(html)
    return builder.root


def all_nodes(root: Node) -> list[Node]:
    return root.descendants()


def first_node(nodes: list[Node], predicate: Callable[[Node], bool]) -> Node | None:
    return next((node for node in nodes if predicate(node)), None)


def class_contains(node: Node, fragment: str) -> bool:
    return any(fragment.lower() in name.lower() for name in node.classes())


def top_level_in_section(node: Node, section_nodes: set[int], tag: str) -> bool:
    parent = node.parent
    while parent is not None and id(parent) in section_nodes:
        if parent.tag == tag:
            return False
        parent = parent.parent
    return True


def section(nodes: list[Node], heading_text: str, stop_tags: set[str] | None = None) -> list[Node]:
    start = next((index for index, node in enumerate(nodes) if node.tag in {"h2", "h3"} and heading_text in node.text()), None)
    if start is None:
        return []
    stop_tags = stop_tags or {"h2"}
    end = len(nodes)
    for index in range(start + 1, len(nodes)):
        if nodes[index].tag in stop_tags:
            end = index
            break
    return nodes[start + 1 : end]


def recipe_identity(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise RecipeParseError("SOURCE_URL_NOT_ALLOWED")
    for pattern in RECIPE_PATHS:
        match = pattern.fullmatch(parsed.path)
        if match:
            canonical = urlunparse(("https", parsed.hostname, parsed.path, "", "", ""))
            return match.group(1), canonical
    raise RecipeParseError("SOURCE_RECIPE_PATH_INVALID")


def node_by_class(nodes: list[Node], *fragments: str) -> Node | None:
    return first_node(nodes, lambda node: any(class_contains(node, fragment) for fragment in fragments))


def parse_author(nodes: list[Node]) -> str | None:
    candidate = first_node(
        nodes,
        lambda node: node.attrs.get("itemprop") == "author" or class_contains(node, "author") or class_contains(node, "recipe_De_title"),
    )
    if candidate is None:
        return None
    author_node = first_node(candidate.descendants(), lambda node: node.tag == "a") or candidate
    value = clean_text(author_node.text())
    value = re.sub(r"^(?:by\s*)|(?:\s*发布)$", "", value, flags=re.IGNORECASE).strip()
    return value or None


def parse_metadata(nodes: list[Node]) -> dict[str, str | None]:
    result: dict[str, str | None] = {"taste": None, "technique": None, "source_time_label": None, "difficulty": None}
    label_map = {"口味": "taste", "工艺": "technique", "耗时": "source_time_label", "难度": "difficulty"}
    candidates: list[str] = []
    for node in nodes:
        if node.tag != "li" or any(parent.tag == "li" for parent in _parents(node)):
            continue
        value = node.text()
        label = node.attrs.get("data-label")
        if label in label_map:
            result[label_map[label]] = value
            continue
        label_node = first_node(
            node.descendants(),
            lambda item: class_contains(item, "category_s2") and item.text() in label_map,
        )
        if label_node is not None:
            value_node = first_node(
                node.descendants(),
                lambda item: class_contains(item, "category_s1"),
            )
            if value_node is not None:
                result[label_map[label_node.text()]] = value_node.text()
                continue
        matched = False
        for chinese_label, key in label_map.items():
            if value.endswith(chinese_label):
                result[key] = value[: -len(chinese_label)].strip()
                matched = True
                break
        if not matched and value:
            candidates.append(value)
    for value in candidates:
        if result["source_time_label"] is None and re.search(r"(?:分钟|小时|刻钟|一天|数天)", value):
            result["source_time_label"] = value
        elif result["difficulty"] is None and value in {"简单", "普通", "初级", "中级", "高级", "神级"}:
            result["difficulty"] = value
        elif result["technique"] is None and value in {"炒", "烧", "煮", "炖", "蒸", "炸", "煎", "烤", "拌", "焖", "卤", "其他"}:
            result["technique"] = value
        elif result["taste"] is None and (value.endswith("味") or value in {"咸鲜", "酸甜", "麻辣", "清淡", "原味"}):
            result["taste"] = value
    return result


def _parents(node: Node) -> list[Node]:
    result: list[Node] = []
    parent = node.parent
    while parent is not None:
        result.append(parent)
        parent = parent.parent
    return result


def ingredient_parts(node: Node) -> tuple[str, str]:
    name = node.attrs.get("data-name", "").strip()
    amount = node.attrs.get("data-amount", "").strip()
    descendants = node.descendants()
    if not name:
        name_node = first_node(descendants, lambda item: class_contains(item, "category_s1") or class_contains(item, "ingredient-name"))
        if name_node:
            name = name_node.text()
    if not amount:
        amount_node = first_node(descendants, lambda item: class_contains(item, "category_s2") or class_contains(item, "ingredient-amount"))
        if amount_node:
            amount = amount_node.text()
    if not name:
        direct_parts = [child.text() for child in node.children if child.tag not in {"img"} and child.text()]
        if len(direct_parts) >= 2:
            name, amount = direct_parts[0], amount or direct_parts[1]
        else:
            raw = node.text()
            match = AMOUNT_SUFFIX.fullmatch(raw)
            if match:
                name, amount = match.group(1).strip(), amount or match.group(2).strip()
            else:
                name = raw
    return clean_text(name), clean_text(amount)


def parse_ingredients(nodes: list[Node]) -> list[RawRecipeIngredient]:
    heading = first_node(nodes, lambda node: node.tag in {"h2", "h3"} and "食材明细" in node.text())
    stop_tags = {"h2"} if heading is not None and heading.tag == "h2" else {"h2", "h3"}
    ingredients_section = section(nodes, "食材明细", stop_tags)
    section_ids = {id(node) for node in ingredients_section}
    has_particulars = any(node.tag == "fieldset" and class_contains(node, "particulars") for node in ingredients_section)
    group = "other"
    result: list[RawRecipeIngredient] = []
    for node in ingredients_section:
        if node.tag in {"h3", "legend"} and node.text() in GROUPS:
            group = GROUPS[node.text()]
            continue
        if node.tag != "li" or not top_level_in_section(node, section_ids, "li"):
            continue
        if has_particulars and not any(parent.tag == "fieldset" and class_contains(parent, "particulars") for parent in _parents(node)):
            continue
        name, amount = ingredient_parts(node)
        if amount in {"口味", "工艺", "耗时", "难度"}:
            continue
        if not name:
            continue
        raw_text = clean_text(f"{name} {amount}")
        result.append(RawRecipeIngredient(group=group, raw_name=name, raw_amount=amount, raw_text=raw_text))
    return result


def parse_steps(nodes: list[Node]) -> list[CookingStep]:
    step_section = section(nodes, "做法步骤", {"h2", "h3"})
    section_ids = {id(node) for node in step_section}
    values: list[tuple[int | None, str]] = []
    for node in step_section:
        if node.tag != "li" or not top_level_in_section(node, section_ids, "li"):
            continue
        content_node = first_node(
            node.descendants(),
            lambda item: class_contains(item, "recipeStep_word") or class_contains(item, "step-content") or item.attrs.get("itemprop") == "recipeInstructions",
        )
        value = content_node.text() if content_node else node.text()
        number_node = first_node(
            content_node.descendants() if content_node else node.descendants(),
            lambda item: class_contains(item, "grey") and item.text().isdigit(),
        )
        if number_node is not None:
            number_text = number_node.text()
            value = re.sub(rf"(?:^|\s){re.escape(number_text)}(?:[.、．:]|\s)*$", "", value).strip()
        match = STEP_PREFIX.match(value)
        number = int(number_node.text()) if number_node is not None else (int(match.group(1)) if match else None)
        instruction = STEP_PREFIX.sub("", value).strip()
        if instruction:
            values.append((number, instruction))
    if not values:
        for node in step_section:
            if node.tag != "p":
                continue
            match = STEP_PREFIX.match(node.text())
            if match:
                values.append((int(match.group(1)), STEP_PREFIX.sub("", node.text()).strip()))
    deduplicated: list[str] = []
    for _, instruction in values:
        if instruction and instruction not in deduplicated:
            deduplicated.append(instruction)
    return [CookingStep(step_number=index, instruction=value) for index, value in enumerate(deduplicated, start=1)]


def parse_tips(nodes: list[Node]) -> list[str]:
    tips_nodes = section(nodes, "小窍门", {"h2", "h3"})
    values = [node.text() for node in tips_nodes if node.tag in {"p", "li"} and node.text()]
    return list(dict.fromkeys(values))


def parse_categories(nodes: list[Node]) -> list[str]:
    categories: list[str] = []
    for node in nodes:
        if node.tag not in {"p", "div", "span"}:
            continue
        value = node.text()
        category_match = re.match(r"(?:所属)?分类[：:]\s*(.+)", value)
        if category_match:
            categories = [item for item in re.split(r"\s+", category_match.group(1)) if item]
    return list(dict.fromkeys(categories))


def parse_recipe_page(html: str, source_url: str, captured_at: datetime | None = None) -> RawMeishiChinaRecipe:
    source_number, canonical_url = recipe_identity(source_url)
    root = parse_tree(html)
    nodes = all_nodes(root)
    title_node = first_node(nodes, lambda node: node.tag == "h1" and class_contains(node, "title") and node.text())
    if title_node is None:
        title_node = first_node(nodes, lambda node: node.tag == "h1" and node.text() and node.text() != "菜谱")
    if title_node is None:
        raise RecipeParseError("RECIPE_TITLE_MISSING")
    ingredients = parse_ingredients(nodes)
    steps = parse_steps(nodes)
    if not ingredients:
        raise RecipeParseError("RECIPE_INGREDIENTS_MISSING")
    if not steps:
        raise RecipeParseError("RECIPE_STEPS_MISSING")
    metadata = parse_metadata(nodes)
    tips = parse_tips(nodes)
    categories = parse_categories(nodes)
    page_text = root.text()
    copyright_match = COPYRIGHT_TEXT.search(page_text)
    warnings: list[str] = []
    if any(not ingredient.raw_amount for ingredient in ingredients):
        warnings.append("MISSING_INGREDIENT_QUANTITY")
    if any(IMAGE_DEPENDENT.search(step.instruction) for step in steps):
        warnings.append("IMAGE_DEPENDENT_STEP")
    if MEDICAL_CLAIM.search(" ".join([*(step.instruction for step in steps), *tips])):
        warnings.append("MEDICAL_CLAIM_PRESENT")
    if metadata["source_time_label"] is None:
        warnings.append("AMBIGUOUS_DURATION")
    author = parse_author(nodes)
    if author is None:
        warnings.append("AUTHOR_MISSING")
    if copyright_match:
        warnings.append("COPYRIGHT_RESTRICTION_PRESENT")
    content_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
    return RawMeishiChinaRecipe(
        staging_id=f"raw-mc-{content_hash[:24]}",
        captured_at=captured_at or datetime.now(timezone.utc),
        source_id=f"meishichina:{source_number}",
        source_url=canonical_url,
        raw_content_hash=content_hash,
        title=title_node.text(),
        author=author,
        ingredients=ingredients,
        cooking_steps=steps,
        taste=metadata["taste"],
        technique=metadata["technique"],
        source_time_label=metadata["source_time_label"],
        difficulty=metadata["difficulty"],
        tips=tips,
        categories=categories,
        copyright_notice=copyright_match.group(0).strip() if copyright_match else None,
        warnings=list(dict.fromkeys(warnings)),
    )


def discover_recipe_urls(html: str, page_url: str, limit: int = 20) -> list[str]:
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    parsed_page = urlparse(page_url)
    if parsed_page.scheme != "https" or parsed_page.hostname not in ALLOWED_HOSTS:
        raise RecipeParseError("CATEGORY_URL_NOT_ALLOWED")
    nodes = all_nodes(parse_tree(html))
    discovered: list[str] = []
    for node in nodes:
        if node.tag != "a" or not node.attrs.get("href"):
            continue
        candidate = urljoin(page_url, node.attrs["href"])
        try:
            _, canonical = recipe_identity(candidate)
        except RecipeParseError:
            continue
        if canonical not in discovered:
            discovered.append(canonical)
        if len(discovered) >= limit:
            break
    return discovered


def discover_next_page(html: str, page_url: str) -> str | None:
    parsed_page = urlparse(page_url)
    nodes = all_nodes(parse_tree(html))
    for node in nodes:
        if node.tag != "a" or "下一页" not in node.text() or not node.attrs.get("href"):
            continue
        candidate = urlparse(urljoin(page_url, node.attrs["href"]))
        if candidate.scheme == "https" and candidate.hostname == parsed_page.hostname:
            return urlunparse((candidate.scheme, candidate.netloc, candidate.path, "", "", ""))
    return None
