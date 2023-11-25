import graphene
from graphene_django import DjangoObjectType
from tracker.models import Position, Wallet, Contract, Network


class PositionType(DjangoObjectType):
    class Meta:
        model = Position


class WalletType(DjangoObjectType):
    class Meta:
        model = Wallet


class ContractType(DjangoObjectType):
    class Meta:
        model = Contract


class NetworkType(DjangoObjectType):
    class Meta:
        model = Network


class TrackerQuery(graphene.ObjectType):
    positions = graphene.List(PositionType)
    wallets = graphene.List(WalletType)

    def resolve_positions(self, info, **kwargs):
        return Position.objects.filter(deleted=False)

    def resolve_wallets(self, info, **kwargs):
        return Wallet.objects.filter(deleted=False)

    def resolve_contracts(self, info, **kwargs):
        return Contract.objects.filter(deleted=False)

    def resolve_networks(self, info, **kwargs):
        return Network.objects.filter(deleted=False)


schema = graphene.Schema(query=TrackerQuery)
