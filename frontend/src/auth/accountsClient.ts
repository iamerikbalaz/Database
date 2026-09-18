import { request } from "../api/client";
import { parseList, uuid } from "../api/dto";
import { internalUserFromDto, parseInternalUser } from "../api/materialDto";
import type { Role } from "./client";

export const accountsClient = {
  async list() {
    return parseList(await request("/internal-users"), parseInternalUser).map(internalUserFromDto);
  },
  async create(display_name: string, email: string, role: Role, requestKey?: string) {
    return internalUserFromDto(parseInternalUser(await request("/internal-users", "POST", { display_name, email, role }, requestKey)));
  },
  async update(userId: string, role: Role, is_active: boolean, requestKey?: string) {
    return internalUserFromDto(parseInternalUser(await request(`/internal-users/${uuid(userId)}`, "PATCH", { role, is_active }, requestKey)));
  },
};
