from __future__ import annotations

import logging
from math import ceil
from typing import TYPE_CHECKING, Any, Literal

import discord

from .magnet import format_size

from .search_utils import get_indexer_name, get_title, parse_positive_int, truncate

if TYPE_CHECKING:
    from .result_delivery import ResultDeliveryService

LOGGER = logging.getLogger("discord_prowlarr_bot")
RESULTS_PER_PAGE = 10

SortKey = Literal["seeders", "size", "publishDate"]


def _sort_results(results: list[dict[str, Any]], sort_key: SortKey) -> list[dict[str, Any]]:
    if sort_key == "seeders":
        return sorted(results, key=lambda r: parse_positive_int(r.get("seeders")), reverse=True)
    if sort_key == "size":
        return sorted(results, key=lambda r: parse_positive_int(r.get("size")), reverse=True)
    return sorted(results, key=lambda r: str(r.get("publishDate") or ""), reverse=True)


class SearchSelect(discord.ui.Select):
    def __init__(self, view: "SearchView") -> None:
        self.search_view = view
        super().__init__(
            placeholder="Elegí un resultado de esta página",
            min_values=1,
            max_values=1,
            options=view.build_options(),
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_index = int(self.values[0])
        await self.search_view.handle_selection(interaction, selected_index)


class SearchView(discord.ui.View):
    def __init__(
        self,
        results: list[dict[str, Any]],
        query: str,
        delivery_service: "ResultDeliveryService",
        author_id: int | None = None,
        ephemeral: bool = False,
    ) -> None:
        super().__init__(timeout=600)
        self.query = query
        self.delivery_service = delivery_service
        self.author_id = author_id
        self.ephemeral = ephemeral
        self.current_page = 0
        self.sort_key: SortKey = "seeders"
        self.results = _sort_results(results, self.sort_key)
        self.message: discord.Message | None = None

        self.sort_seeders_button = discord.ui.Button(label="🌱 Seeders", row=0)
        self.sort_seeders_button.callback = self._make_sort_callback("seeders")
        self.add_item(self.sort_seeders_button)

        self.sort_size_button = discord.ui.Button(label="📦 Tamaño", row=0)
        self.sort_size_button.callback = self._make_sort_callback("size")
        self.add_item(self.sort_size_button)

        self.sort_date_button = discord.ui.Button(label="🗓️ Fecha", row=0)
        self.sort_date_button.callback = self._make_sort_callback("publishDate")
        self.add_item(self.sort_date_button)

        self.previous_button = discord.ui.Button(
            label="⬅️ Anterior",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        self.previous_button.callback = self.on_previous
        self.add_item(self.previous_button)

        self.next_button = discord.ui.Button(
            label="➡️ Siguiente",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        self.next_button.callback = self.on_next
        self.add_item(self.next_button)

        self.result_select: SearchSelect | None = None
        self.refresh_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.author_id is None or interaction.user.id == self.author_id:
            return True

        await interaction.response.send_message(
            "Solo el autor de la búsqueda puede interactuar con esta vista.",
            ephemeral=True,
        )
        return False

    @property
    def total_pages(self) -> int:
        return max(1, ceil(len(self.results) / RESULTS_PER_PAGE))

    def page_bounds(self) -> tuple[int, int]:
        start = self.current_page * RESULTS_PER_PAGE
        end = start + RESULTS_PER_PAGE
        return start, end

    def build_options(self) -> list[discord.SelectOption]:
        start, end = self.page_bounds()
        options: list[discord.SelectOption] = []

        for index, result in enumerate(self.results[start:end], start=start):
            title = get_title(result)
            seeders = parse_positive_int(result.get("seeders"))
            size_human = format_size(result.get("size"))
            description = truncate(
                f"{size_human} · {seeders} seeders · {get_indexer_name(result)}",
                100,
            )

            options.append(
                discord.SelectOption(
                    label=truncate(title, 100),
                    description=description,
                    value=str(index),
                )
            )

        return options

    def _update_sort_button_styles(self) -> None:
        mapping = {
            "seeders": self.sort_seeders_button,
            "size": self.sort_size_button,
            "publishDate": self.sort_date_button,
        }
        for key, button in mapping.items():
            button.style = (
                discord.ButtonStyle.primary if key == self.sort_key else discord.ButtonStyle.secondary
            )

    def refresh_components(self) -> None:
        self._update_sort_button_styles()
        self.previous_button.disabled = self.current_page <= 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1

        if self.result_select is not None:
            self.remove_item(self.result_select)

        self.result_select = SearchSelect(self)
        self.add_item(self.result_select)

    def build_embed(self) -> discord.Embed:
        start, end = self.page_bounds()
        lines: list[str] = []

        for display_index, result in enumerate(self.results[start:end], start=start + 1):
            title = truncate(get_title(result), 200)
            size_human = format_size(result.get("size"))
            seeders = parse_positive_int(result.get("seeders"))
            indexer = get_indexer_name(result)

            lines.append(
                f"`{display_index}.` **{title}**\n"
                f"📦 {size_human} · 🌱 {seeders} · 🧲 {indexer}"
            )

        embed = discord.Embed(
            title=truncate(f"Resultados para: {self.query}", 256),
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"Página {self.current_page + 1}/{self.total_pages} · {len(self.results)} resultados"
        )
        return embed

    def _make_sort_callback(self, sort_key: SortKey):
        async def callback(interaction: discord.Interaction) -> None:
            if self.sort_key != sort_key:
                self.sort_key = sort_key
                self.results = _sort_results(self.results, sort_key)
                self.current_page = 0
            self.refresh_components()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        return callback

    async def on_previous(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
        self.refresh_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_next(self, interaction: discord.Interaction) -> None:
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        self.refresh_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def handle_selection(self, interaction: discord.Interaction, selected_index: int) -> None:
        if selected_index < 0 or selected_index >= len(self.results):
            await interaction.response.send_message(
                "❌ Ese resultado ya no está disponible. Ejecutá la búsqueda de nuevo.",
                ephemeral=True,
            )
            return

        result = self.results[selected_index]
        title = get_title(result)
        await interaction.response.edit_message(
            content=f"⏱️ **Preparando entrega: {truncate(title, 160)}**\n```text\n  0.0s Selección recibida\n```",
            embed=None,
            view=None,
        )
        progress_message = self.message
        if progress_message is None:
            try:
                progress_message = await interaction.original_response()
            except discord.HTTPException:
                progress_message = None

        self.message = None
        self.stop()

        await self.delivery_service.deliver_result(
            interaction,
            result,
            author_id=self.author_id,
            progress_message=progress_message,
            ephemeral=self.ephemeral,
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

        if self.message is None:
            return

        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            LOGGER.warning("No se pudo deshabilitar la vista expirada para '%s'.", self.query)
