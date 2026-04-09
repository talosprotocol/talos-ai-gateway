"""Pydantic models for the A2A v1 discovery and RPC surface."""

from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, Field


class AgentProvider(BaseModel):
    organization: str
    url: Optional[str] = None


class AgentInterface(BaseModel):
    url: str
    protocolBinding: str
    protocolVersion: str


class AgentExtension(BaseModel):
    uri: str
    description: str
    required: bool = False


class AgentCapabilities(BaseModel):
    streaming: bool = True
    pushNotifications: bool = True
    extensions: List[AgentExtension] = Field(default_factory=list)
    extendedAgentCard: bool = False


class SecurityScheme(BaseModel):
    type: str
    scheme: Optional[str] = None
    bearerFormat: Optional[str] = None


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    tags: List[str] = Field(default_factory=list)
    examples: List[str] = Field(default_factory=list)
    inputModes: List[str] = Field(default_factory=list)
    outputModes: List[str] = Field(default_factory=list)
    securityRequirements: List[Dict[str, List[str]]] = Field(default_factory=list)


class AgentCard(BaseModel):
    name: str
    description: str
    version: str
    provider: AgentProvider
    documentationUrl: str
    supportedInterfaces: List[AgentInterface]
    defaultInputModes: List[str] = Field(default_factory=lambda: ["text"])
    defaultOutputModes: List[str] = Field(default_factory=lambda: ["text"])
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    skills: List[AgentSkill] = Field(default_factory=list)
    securitySchemes: Dict[str, SecurityScheme]
    securityRequirements: List[Dict[str, List[str]]] = Field(default_factory=list)


class TextPart(BaseModel):
    text: str


class AuthenticationInfo(BaseModel):
    scheme: str
    credentials: Optional[str] = None


class Message(BaseModel):
    messageId: str
    role: str
    parts: List[TextPart]
    taskId: Optional[str] = None
    contextId: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class TaskStatus(BaseModel):
    state: str
    timestamp: str
    message: Optional[Message] = None


class Artifact(BaseModel):
    artifactId: str
    parts: List[TextPart]
    name: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class Task(BaseModel):
    id: str
    version: str = "1.0"
    contextId: str
    status: TaskStatus
    history: Optional[List[Message]] = None
    artifacts: Optional[List[Artifact]] = None
    metadata: Optional[Dict[str, Any]] = None


class SendMessageConfiguration(BaseModel):
    acceptedOutputModes: List[str] = Field(default_factory=list)
    historyLength: Optional[int] = Field(default=None, ge=0)
    returnImmediately: bool = False
    taskPushNotificationConfig: Optional["TaskPushNotificationConfig"] = None
    pushNotificationConfig: Optional["TaskPushNotificationConfig"] = None
    model_config = {"extra": "allow"}


class SendMessageParams(BaseModel):
    message: Dict[str, Any]
    configuration: Optional[SendMessageConfiguration] = None
    metadata: Optional[Dict[str, Any]] = None
    model_config = {"extra": "allow"}


class GetTaskParams(BaseModel):
    id: str
    historyLength: Optional[int] = Field(default=None, ge=0)
    includeArtifacts: bool = False
    model_config = {"extra": "allow"}


class CancelTaskParams(BaseModel):
    id: str
    historyLength: Optional[int] = Field(default=None, ge=0)
    includeArtifacts: bool = False
    model_config = {"extra": "allow"}


class ListTasksParams(BaseModel):
    contextId: Optional[str] = None
    status: Optional[str] = Field(default=None, validation_alias=AliasChoices("status", "state"))
    historyLength: Optional[int] = Field(default=None, ge=0)
    includeArtifacts: bool = False
    pageSize: int = Field(default=50, ge=1, le=100)
    pageToken: Optional[str] = None
    statusTimestampAfter: Optional[str] = None
    model_config = {"extra": "allow"}


class SubscribeToTaskParams(BaseModel):
    id: str
    historyLength: Optional[int] = Field(default=None, ge=0)
    includeArtifacts: bool = False
    model_config = {"extra": "allow"}


class TaskStatusUpdateEvent(BaseModel):
    taskId: str
    contextId: str
    status: TaskStatus
    metadata: Optional[Dict[str, Any]] = None


class TaskArtifactUpdateEvent(BaseModel):
    taskId: str
    contextId: str
    artifact: Artifact
    append: bool = False
    lastChunk: bool = True
    metadata: Optional[Dict[str, Any]] = None


class StreamResponse(BaseModel):
    task: Optional[Task] = None
    message: Optional[Message] = None
    statusUpdate: Optional[TaskStatusUpdateEvent] = None
    artifactUpdate: Optional[TaskArtifactUpdateEvent] = None


class ListTasksResponse(BaseModel):
    tasks: List[Task] = Field(default_factory=list)
    nextPageToken: Optional[str] = None
    pageSize: int
    totalSize: int


class TaskPushNotificationConfig(BaseModel):
    id: Optional[str] = None
    taskId: Optional[str] = None
    url: str
    token: Optional[str] = None
    authentication: Optional[AuthenticationInfo] = None


class GetTaskPushNotificationConfigParams(BaseModel):
    taskId: str
    id: str
    model_config = {"extra": "allow"}


class ListTaskPushNotificationConfigsParams(BaseModel):
    taskId: str
    model_config = {"extra": "allow"}


class DeleteTaskPushNotificationConfigParams(BaseModel):
    taskId: str
    id: str
    model_config = {"extra": "allow"}


class ListTaskPushNotificationConfigsResponse(BaseModel):
    configs: List[TaskPushNotificationConfig] = Field(default_factory=list)
    nextPageToken: Optional[str] = None
