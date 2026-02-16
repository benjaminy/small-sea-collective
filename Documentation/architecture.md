# Small Sea Collective Design and Implementation

Small Sea Collective is a framework for making team collaboration applications.
Stuff like Microsoft Teams, Google Workspace, Nextcloud, Slack, etc.
The unique feature of Small Sea is that there are no special "Small Sea" services running on the internet.

The big idea is that individuals subscribe to commodity cloud services like Dropbox for storage.
The Small Sea framework provides a kind of local-first hub/gateway for applications to use
Individuals get general purpose services from anywhere.

One way to summarize Small Sea is that it brings the Local-First paradigm and to team collaboration platforms.

## Core Concepts

The core abstractions in Small Sea are:

- Team
   - This is the core of any collaboration framework, of course
   - The main thing that's different in Small Sea is that there is no central service.
     So team management (like everything else) is decentralized and collaborative.
- Application
   - "Apps" are not specific client software; rather they're a way to organize resources like storage, notifications, etc.
- Station
   - A station the intersection of a particular team and a particular app
   - This is the fundamental unit of resource allocation and access control
- Client
   - Any software that accesses resources through small sea
   - Clients first have to request access to whatever stations

## Small Sea Hub

The Small Sea Hub is a process that runs locally and provides access to network services (storage, streaming connections, etc) for Small Sea clients running on the machine.
In a world where Small Sea is very successful, the Hub plays a really critical role: All the network-level activity for an application (storing files, sending notifications, establishing streaming connections) flows through the Hub.

### Authentication

It is important for client programs to authenticate to some station.
A user who participates in many teams and uses many applications almost certainly doesn't want a client for one application to have full access to other applications.
Before a client program can access any resources, it must send an access request to the Hub.
The Hub will create a token and a pending entry in its access control database.
It also creates a PIN for the request and sends it to the user via OS notification.
The user has to enter the PIN into the client through some UI to complete the access request.
The Hub then sends the token to the client which can use it for subsequent service requests.

## Team Management

