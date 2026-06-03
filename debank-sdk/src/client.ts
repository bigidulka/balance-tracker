import { HttpModule } from "./modules/http";

export interface DeBankClientOptions {}

export class DeBankClient {
  readonly http: HttpModule;

  constructor(_options: DeBankClientOptions = {}) {
    this.http = new HttpModule();
  }
}
