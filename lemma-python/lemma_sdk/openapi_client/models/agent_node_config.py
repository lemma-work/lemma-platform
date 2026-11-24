from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_node_config_input_mapping import AgentNodeConfigInputMapping
    from ..models.expression_input_binding import ExpressionInputBinding
    from ..models.generation_settings import GenerationSettings
    from ..models.literal_input_binding import LiteralInputBinding


T = TypeVar("T", bound="AgentNodeConfig")


@_attrs_define
class AgentNodeConfig:
    """Configuration for Agent node.

    Attributes:
        agent_name (str): Agent resource name to execute.
        generation (GenerationSettings | None | Unset): How the model generates for this step: reasoning effort, output
            cap, temperature. Each field set here replaces the agent's; each left unset keeps it.
        input_mapping (AgentNodeConfigInputMapping | Unset): Explicit mapping from agent input key to either an
            expression or a literal JSON value. Strings are never auto-interpreted. Example: {'channel': {'type': 'literal',
            'value': 'finance'}, 'issue_key': {'type': 'expression', 'value': 'start.payload.issue.key'}}.
        instructions (None | str | Unset): What this step is for. Added to the agent's own instructions for this run
            only; the agent keeps its identity and gains a task.
        model_name (None | str | Unset): Another model from the agent's runtime profile, for this step only. Unset runs
            on the agent's model.
        ref (ExpressionInputBinding | LiteralInputBinding | None | Unset): Where that worktree starts: a branch, a tag,
            a commit sha, or a pull request's head as `pull/<n>/head`. Unset is the repository's default branch. Needs
            `repository`.
        repository (ExpressionInputBinding | LiteralInputBinding | None | Unset): The GitHub repository the agent works
            in, as `owner/name`. The run starts in a worktree of it, cloned through the pod's `github` connector as the
            run's person, as a webhook-started run does. Usually an expression, e.g. `start.payload.repository.full_name`.
        timeout_seconds (int | None | Unset): How long the step may run before the workflow stops it, in place of the
            deployment's ceiling. Checked by a periodic sweep, so the step ends within minutes of it, not to the second. A
            step waiting on a person gets the usual multiple of it.
    """

    agent_name: str
    generation: GenerationSettings | None | Unset = UNSET
    input_mapping: AgentNodeConfigInputMapping | Unset = UNSET
    instructions: None | str | Unset = UNSET
    model_name: None | str | Unset = UNSET
    ref: ExpressionInputBinding | LiteralInputBinding | None | Unset = UNSET
    repository: ExpressionInputBinding | LiteralInputBinding | None | Unset = UNSET
    timeout_seconds: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.expression_input_binding import ExpressionInputBinding
        from ..models.generation_settings import GenerationSettings
        from ..models.literal_input_binding import LiteralInputBinding

        agent_name = self.agent_name

        generation: dict[str, Any] | None | Unset
        if isinstance(self.generation, Unset):
            generation = UNSET
        elif isinstance(self.generation, GenerationSettings):
            generation = self.generation.to_dict()
        else:
            generation = self.generation

        input_mapping: dict[str, Any] | Unset = UNSET
        if not isinstance(self.input_mapping, Unset):
            input_mapping = self.input_mapping.to_dict()

        instructions: None | str | Unset
        if isinstance(self.instructions, Unset):
            instructions = UNSET
        else:
            instructions = self.instructions

        model_name: None | str | Unset
        if isinstance(self.model_name, Unset):
            model_name = UNSET
        else:
            model_name = self.model_name

        ref: dict[str, Any] | None | Unset
        if isinstance(self.ref, Unset):
            ref = UNSET
        elif isinstance(self.ref, ExpressionInputBinding):
            ref = self.ref.to_dict()
        elif isinstance(self.ref, LiteralInputBinding):
            ref = self.ref.to_dict()
        else:
            ref = self.ref

        repository: dict[str, Any] | None | Unset
        if isinstance(self.repository, Unset):
            repository = UNSET
        elif isinstance(self.repository, ExpressionInputBinding):
            repository = self.repository.to_dict()
        elif isinstance(self.repository, LiteralInputBinding):
            repository = self.repository.to_dict()
        else:
            repository = self.repository

        timeout_seconds: int | None | Unset
        if isinstance(self.timeout_seconds, Unset):
            timeout_seconds = UNSET
        else:
            timeout_seconds = self.timeout_seconds

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent_name": agent_name,
            }
        )
        if generation is not UNSET:
            field_dict["generation"] = generation
        if input_mapping is not UNSET:
            field_dict["input_mapping"] = input_mapping
        if instructions is not UNSET:
            field_dict["instructions"] = instructions
        if model_name is not UNSET:
            field_dict["model_name"] = model_name
        if ref is not UNSET:
            field_dict["ref"] = ref
        if repository is not UNSET:
            field_dict["repository"] = repository
        if timeout_seconds is not UNSET:
            field_dict["timeout_seconds"] = timeout_seconds

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_node_config_input_mapping import AgentNodeConfigInputMapping
        from ..models.expression_input_binding import ExpressionInputBinding
        from ..models.generation_settings import GenerationSettings
        from ..models.literal_input_binding import LiteralInputBinding

        d = dict(src_dict)
        agent_name = d.pop("agent_name")

        def _parse_generation(data: object) -> GenerationSettings | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                generation_type_0 = GenerationSettings.from_dict(data)

                return generation_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(GenerationSettings | None | Unset, data)

        generation = _parse_generation(d.pop("generation", UNSET))

        _input_mapping = d.pop("input_mapping", UNSET)
        input_mapping: AgentNodeConfigInputMapping | Unset
        if isinstance(_input_mapping, Unset):
            input_mapping = UNSET
        else:
            input_mapping = AgentNodeConfigInputMapping.from_dict(_input_mapping)

        def _parse_instructions(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        instructions = _parse_instructions(d.pop("instructions", UNSET))

        def _parse_model_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model_name = _parse_model_name(d.pop("model_name", UNSET))

        def _parse_ref(
            data: object,
        ) -> ExpressionInputBinding | LiteralInputBinding | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                ref_type_0_type_0 = ExpressionInputBinding.from_dict(data)

                return ref_type_0_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                ref_type_0_type_1 = LiteralInputBinding.from_dict(data)

                return ref_type_0_type_1
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                ExpressionInputBinding | LiteralInputBinding | None | Unset, data
            )

        ref = _parse_ref(d.pop("ref", UNSET))

        def _parse_repository(
            data: object,
        ) -> ExpressionInputBinding | LiteralInputBinding | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                repository_type_0_type_0 = ExpressionInputBinding.from_dict(data)

                return repository_type_0_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                repository_type_0_type_1 = LiteralInputBinding.from_dict(data)

                return repository_type_0_type_1
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(
                ExpressionInputBinding | LiteralInputBinding | None | Unset, data
            )

        repository = _parse_repository(d.pop("repository", UNSET))

        def _parse_timeout_seconds(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        timeout_seconds = _parse_timeout_seconds(d.pop("timeout_seconds", UNSET))

        agent_node_config = cls(
            agent_name=agent_name,
            generation=generation,
            input_mapping=input_mapping,
            instructions=instructions,
            model_name=model_name,
            ref=ref,
            repository=repository,
            timeout_seconds=timeout_seconds,
        )

        agent_node_config.additional_properties = d
        return agent_node_config

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
